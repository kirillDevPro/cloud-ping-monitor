"""Manager for the pool of monitoring worker processes."""

import logging
from dataclasses import dataclass
from datetime import datetime
from multiprocessing import Event, Lock, Manager, Process, Queue
from multiprocessing.synchronize import Event as EventType
from queue import Empty as QueueEmpty

from ..config import Settings
from ..models import Server
from ..storage import ServersRepository

from .ping_worker import ping_worker_function

logger = logging.getLogger(__name__)


@dataclass
class WorkerInfo:
    """Bookkeeping for a single monitored worker process.

    Attributes:
        server_id: Composite key of the monitored server.
        process: Multiprocessing process that performs ping checks.
        started_at: Time the current worker process was started.
        stop_event: Per-worker event used to request graceful shutdown.
        restart_count: Consecutive successful restarts since the last healthy reset.
        last_restart: Time of the last successful restart, if any.
    """

    server_id: str
    process: Process
    started_at: datetime
    stop_event: EventType  # Per-worker stop_event dedicated to this worker
    restart_count: int = 0
    last_restart: datetime | None = None


class PingManager:
    """
    Manager for the pool of monitoring worker processes.

    Owns the worker lifecycle:
    - Starting/stopping workers
    - Monitoring process health
    - Automatically restarting crashed workers with a bounded restart budget
    - Marking exhausted workers abandoned and reconciling them after backoff
    - Exposing subsystem health probes for the background health task
    - Graceful shutdown
    """

    # Maximum number of CONSECUTIVE crashes allowed per worker before giving up
    MAX_RESTART_ATTEMPTS = 5
    # Minimum time between restarts (seconds)
    RESTART_COOLDOWN = 60
    # A worker alive this long since its last restart has its restart budget reset, so
    # MAX_RESTART_ATTEMPTS counts CONSECUTIVE crashes, not lifetime ones — a flaky worker
    # that recovers each time is never permanently abandoned over weeks of uptime.
    RESTART_COUNT_RESET_SECONDS = 600
    # How long an abandoned (gave-up) worker waits before reconcile_monitoring() retries
    # it, so a permanently-broken server is retried periodically without alert spam.
    ABANDON_RETRY_BACKOFF_SECONDS = 1800
    # Capacity of the ping-results IPC queue (sized for 200-300 servers). Exposed so the
    # health task can alert when the queue nears capacity (a wedged/dead consumer).
    PING_QUEUE_MAXSIZE = 5000

    @staticmethod
    def _get_server_key(server: Server) -> str:
        """
        Return the composite key for a server (provider:server_id).

        Used as the shared_state key; it prevents collisions when different
        providers own servers that share the same ID.

        Args:
            server: The server.

        Returns:
            str: Composite key in the format "provider:server_id".
        """
        return server.composite_key

    @staticmethod
    def _group_servers_by_provider(servers: list[Server]) -> dict[str, list[Server]]:
        """
        Group servers by their effective alias (the composite-key identity).

        Grouping by effective_alias (e.g. "hetzner_prod") instead of the bare
        provider type ("hetzner") keeps multi-account startup logs accurate.

        Args:
            servers: List of servers.

        Returns:
            Dict[str, list[Server]]: Mapping {effective_alias: [servers]}.
        """
        from collections import defaultdict

        grouped: dict[str, list[Server]] = defaultdict(list)
        for server in servers:
            grouped[server.effective_alias].append(server)
        return dict(grouped)

    def __init__(self, servers_repo: ServersRepository, settings: Settings):
        """
        Initialize the PingManager.

        Args:
            servers_repo: Servers repository.
            settings: Application configuration.
        """
        self.servers_repo = servers_repo
        self.settings = settings

        # Worker registry: composite_key -> WorkerInfo
        self.workers: dict[str, WorkerInfo] = {}

        # Composite keys whose worker exhausted its restart budget, with the time of
        # abandonment. Used to avoid re-adding a hopeless worker in a tight loop and to
        # retry it on a long backoff via reconcile_monitoring().
        self._abandoned: dict[str, datetime] = {}

        # IPC objects: workers publish results and shared status only; there is
        # no command queue for runtime worker commands.
        self.ping_results_queue: Queue = Queue(maxsize=self.PING_QUEUE_MAXSIZE)

        # Multiprocessing Manager for shared state
        self.manager = Manager()
        self.shared_state = self.manager.dict()

        # Lock guarding read-modify-write operations on shared_state
        self.shared_state_lock = Lock()

        # NOTE: stop_event is now created per worker in
        # WorkerInfo.stop_event (see start_worker())

        # Flag preventing a double manager.shutdown() call
        self._manager_closed = False

        logger.info("PingManager initialized")

    def __del__(self):
        """
        Destructor that guarantees cleanup of the Manager process.

        Called when the PingManager object is garbage collected. Ensures the
        Manager server process is shut down even on unexpected errors.
        """
        try:
            if hasattr(self, "manager") and not getattr(self, "_manager_closed", True):
                self.manager.shutdown()
                self._manager_closed = True
        except Exception as e:
            # Ignore errors in the destructor (may run during Python shutdown).
            # Log at DEBUG level for diagnostics.
            try:
                logger.debug(f"Cleanup error in __del__: {e}")
            except Exception:
                pass  # logger may be unavailable during shutdown

    def start_all_workers(self) -> int:
        """
        Start workers for every server with enabled=True.

        Returns:
            Number of workers started.
        """
        # Fetch the list of servers to monitor
        servers = self.servers_repo.get_all()
        enabled_servers = [s for s in servers if s.enabled]

        # Group servers by provider
        servers_by_provider = self._group_servers_by_provider(enabled_servers)

        # Log the start, grouped by provider
        logger.info(
            f"Starting workers for {len(enabled_servers)} enabled servers "
            f"(total servers: {len(servers)})"
        )
        for provider_name, provider_servers in sorted(servers_by_provider.items()):
            logger.info(f"  - {provider_name}: {len(provider_servers)} servers")

        # Start workers and track successful starts per provider
        from collections import defaultdict

        started_by_provider: dict[str, int] = defaultdict(int)
        started_count = 0

        for server in enabled_servers:
            try:
                # IMPORTANT: use the composite key for correct identification
                composite_key = self._get_server_key(server)
                self.start_worker(composite_key)
                started_count += 1
                started_by_provider[server.effective_alias] += 1
            except Exception as e:
                logger.error(f"Failed to start worker for {server.id}: {e}")

        # Log the final statistics, grouped by provider
        logger.info(f"Started {started_count} worker processes:")
        for provider_name, count in sorted(started_by_provider.items()):
            logger.info(f"  - {provider_name}: {count} workers")

        return started_count

    def start_worker(self, server_id: str) -> None:
        """
        Start a worker for a specific server.

        Args:
            server_id: Server ID or composite key in the format "provider:server_id".

        Raises:
            ValueError: If the server is not found or is disabled.
        """
        # Skip if a worker is already running
        if server_id in self.workers:
            logger.warning(f"Worker for {server_id} already running")
            return

        # Try to resolve the server.
        # First check whether server_id is a composite key (contains ":")
        if ":" in server_id:
            # This is a composite key in the format "provider:server_id"
            server = self.servers_repo.get_by_composite_key(server_id)
        else:
            # This is a plain ID (legacy format) - emit a warning
            logger.warning(
                f"start_worker: server_id '{server_id}' is not in composite_key format. "
                f"Using get_by_id() which may return wrong server if IDs conflict."
            )
            server = self.servers_repo.get_by_id(server_id)

        if not server:
            raise ValueError(f"Server {server_id} not found")

        if not server.enabled:
            raise ValueError(f"Server {server_id} is disabled")

        # IMPORTANT: use the composite key to identify the worker
        composite_key = self._get_server_key(server)

        # Create a PER-WORKER stop_event for this worker.
        # This prevents stop_worker() on one worker from stopping ALL workers.
        worker_stop_event = Event()

        # Resolve provider_alias for the composite key (effective_alias handles legacy)
        provider_alias = server.effective_alias

        # Create the worker Process
        process = Process(
            target=ping_worker_function,
            args=(
                server.id,
                server.ip,
                provider_alias,  # CRITICAL: pass the alias for a correct composite key
                self.ping_results_queue,
                self.shared_state,
                self.shared_state_lock,
                worker_stop_event,  # Per-worker stop_event
                self.settings.PING_INTERVAL,
                self.settings.PING_TIMEOUT,
                self.settings.PING_ATTEMPTS,
            ),
            name=f"worker_{composite_key.replace(':', '_')}",
        )

        # Start the process
        process.start()

        # CRITICAL: register the worker under its composite key with its own stop_event
        self.workers[composite_key] = WorkerInfo(
            server_id=composite_key,
            process=process,
            started_at=datetime.now(),
            stop_event=worker_stop_event,
        )

        logger.debug(f"Worker started for {composite_key} ({server.name}), " f"PID={process.pid}")

    @staticmethod
    def _terminate_process(process: Process, server_id: str, timeout: int) -> None:
        """
        Cascade process shutdown: graceful join -> terminate -> kill -> zombie check.

        Assumes the worker's stop_event has already been set by the caller, so it
        starts by waiting for voluntary termination.

        Args:
            process: The worker process.
            server_id: The worker's composite key (for logging).
            timeout: Time to wait for graceful termination (seconds).
        """
        process.join(timeout=timeout)

        if process.is_alive():
            logger.warning(f"Worker {server_id} did not stop gracefully, terminating")
            process.terminate()
            process.join(timeout=5)

        if process.is_alive():
            logger.error(f"Worker {server_id} did not terminate, killing")
            process.kill()
            process.join(timeout=2)

            # Zombie check - process still alive after SIGKILL
            if process.is_alive():
                logger.critical(
                    f"ZOMBIE PROCESS: Worker {server_id} (PID={process.pid}) "
                    f"did not die after SIGKILL! Manual intervention required."
                )

    def stop_worker(self, server_id: str, timeout: int = 30) -> None:
        """
        Stop the worker for a specific server.

        Args:
            server_id: Server ID or composite key in the format "provider:server_id".
            timeout: Maximum time to wait for termination (seconds).
        """
        # Try to look up the worker by key.
        # First try server_id as-is (it may already be a composite key)
        worker_info = self.workers.get(server_id)

        # If not found, try to resolve the server and build the composite key
        if not worker_info:
            # Require composite_key format for unambiguous identification
            if ":" not in server_id:
                logger.error(
                    f"stop_worker requires composite_key format (provider_alias:id), "
                    f"got: {server_id}"
                )
                return

            server = self.servers_repo.get_by_composite_key(server_id)

            if server:
                composite_key = self._get_server_key(server)
                worker_info = self.workers.get(composite_key)
                # Switch server_id to the composite key for the rest of the method
                server_id = composite_key

        if not worker_info:
            logger.warning(f"Worker for {server_id} not found")
            # Prune any stale abandonment mark (e.g. an already-abandoned worker now being
            # removed) so _abandoned does not retain entries for gone servers.
            self._abandoned.pop(server_id, None)
            return

        process = worker_info.process

        logger.info(f"Stopping worker for {server_id}, PID={process.pid}")

        # Graceful shutdown via the PER-WORKER stop_event, then the termination cascade
        worker_info.stop_event.set()
        self._terminate_process(process, server_id, timeout)

        # Remove from the workers registry
        del self.workers[server_id]

        # Close the process handle (critical on Windows)
        try:
            process.close()
        except Exception:
            pass

        # Clear shared_state (server_id is now guaranteed to be a composite key).
        # Use the Lock to guard against a race with worker processes.
        with self.shared_state_lock:
            if server_id in self.shared_state:
                del self.shared_state[server_id]

        # An explicitly stopped worker is not abandoned; drop any stale mark.
        self._abandoned.pop(server_id, None)

        logger.info(f"Worker stopped for {server_id}")

    def monitor_workers(self) -> list[str]:
        """
        Check the health of all workers, restart crashed ones, and reset the restart
        budget of workers that have run healthy long enough.

        Intended to be called periodically by workers_health_task.

        Returns:
            list[str]: Composite keys whose worker exhausted MAX_RESTART_ATTEMPTS this
                cycle and was abandoned. The caller alerts administrators about these.
        """
        abandoned: list[str] = []
        now = datetime.now()

        for server_id, worker_info in list(self.workers.items()):
            if worker_info.process.is_alive():
                # A worker alive (no restart) for long enough has its restart budget
                # reset, so MAX_RESTART_ATTEMPTS counts CONSECUTIVE crashes rather than
                # lifetime ones — a flaky-but-recovering worker is never abandoned.
                if (
                    worker_info.restart_count > 0
                    and worker_info.last_restart is not None
                    and (now - worker_info.last_restart).total_seconds()
                    >= self.RESTART_COUNT_RESET_SECONDS
                ):
                    worker_info.restart_count = 0
                    worker_info.last_restart = None
                    logger.info(
                        f"Worker for {server_id} healthy for "
                        f"{self.RESTART_COUNT_RESET_SECONDS}s; restart budget reset"
                    )
                continue

            logger.warning(f"Worker for {server_id} crashed (PID={worker_info.process.pid})")

            # Restart limit reached: give up and abandon this worker (caller alerts).
            if worker_info.restart_count >= self.MAX_RESTART_ATTEMPTS:
                logger.error(
                    f"Worker for {server_id} exceeded restart limit "
                    f"({self.MAX_RESTART_ATTEMPTS} attempts), giving up"
                )
                del self.workers[server_id]
                self._mark_abandoned(server_id, now)
                abandoned.append(server_id)
                continue

            # Check the cooldown
            if worker_info.last_restart:
                elapsed = now - worker_info.last_restart
                if elapsed.total_seconds() < self.RESTART_COOLDOWN:
                    logger.warning(
                        f"Too soon to restart {server_id}, "
                        f"waiting for cooldown "
                        f"({self.RESTART_COOLDOWN - elapsed.total_seconds():.0f}s remaining)"
                    )
                    continue

            # Keep a reference before deletion so we can close the handle
            old_process = worker_info.process

            # Remove the old worker
            del self.workers[server_id]

            # Close the process handle (critical on Windows)
            try:
                old_process.close()
            except Exception:
                pass

            # Restart (narrow the try to start_worker so a post-restart
            # accounting/log error isn't mislogged as a restart failure)
            try:
                self.start_worker(server_id)
            except Exception as e:
                logger.error(f"Failed to restart worker for {server_id}: {e}")
            else:
                # Update the restart counter only after a successful start
                new_worker_info = self.workers[server_id]
                new_worker_info.restart_count = worker_info.restart_count + 1
                new_worker_info.last_restart = datetime.now()

                logger.info(
                    f"Worker for {server_id} restarted "
                    f"({new_worker_info.restart_count}/{self.MAX_RESTART_ATTEMPTS})"
                )

        return abandoned

    def _mark_abandoned(self, server_id: str, when: datetime) -> None:
        """
        Record a worker as abandoned and clear its shared_state entry.

        Clearing shared_state (mirroring stop_worker) stops the dashboard from showing a
        frozen status for a server that is no longer monitored; the recorded timestamp
        lets reconcile_monitoring() retry the worker only after ABANDON_RETRY_BACKOFF_SECONDS.

        Args:
            server_id: Composite key of the abandoned worker.
            when: Time of abandonment.
        """
        self._abandoned[server_id] = when
        with self.shared_state_lock:
            if server_id in self.shared_state:
                del self.shared_state[server_id]

    def reconcile_monitoring(self, enabled_servers: list[Server]) -> list[str]:
        """
        (Re)start monitoring for enabled servers that have no live worker.

        Heals what the periodic crash-restart loop cannot: a worker that failed to start
        at boot, a worker abandoned after exhausting its restart budget (retried only
        after ABANDON_RETRY_BACKOFF_SECONDS so a hopeless server is not re-added in a tight
        loop), and any enabled server simply not being monitored. A re-added worker gets a
        fresh WorkerInfo (restart_count=0), so its restart budget is reset.

        Args:
            enabled_servers: Enabled servers from the repository (the desired monitored set).

        Returns:
            list[str]: Composite keys for which a worker was (re)started this cycle.
        """
        now = datetime.now()
        enabled_keys = {server.composite_key for server in enabled_servers}

        # Prune abandon marks for servers no longer in the enabled set (removed/disabled)
        # so _abandoned cannot grow without bound across fleet churn. A server that is
        # re-enabled later starts fresh (it should resume monitoring at once, not wait out
        # a stale backoff).
        for stale_key in [k for k in self._abandoned if k not in enabled_keys]:
            self._abandoned.pop(stale_key, None)

        restarted: list[str] = []

        for server in enabled_servers:
            key = server.composite_key

            if self.is_monitoring(key):
                # Monitored again: drop any stale abandonment mark.
                self._abandoned.pop(key, None)
                continue

            # Not monitored. If recently abandoned, wait out the retry backoff.
            abandoned_at = self._abandoned.get(key)
            if (
                abandoned_at is not None
                and (now - abandoned_at).total_seconds() < self.ABANDON_RETRY_BACKOFF_SECONDS
            ):
                continue

            try:
                self.start_worker(key)
            except Exception as e:
                logger.error(f"Reconcile: failed to start worker for {key}: {e}", exc_info=True)
                continue

            self._abandoned.pop(key, None)
            restarted.append(key)
            logger.info(f"Reconcile: (re)started monitoring for {key}")

        return restarted

    def shutdown_all(self, timeout: int = 30) -> None:
        """
        Gracefully shut down all worker processes.

        Operation order to avoid deadlocks:
        1. Signal ALL workers (set their stop_event)
        2. Drain the queues (prevents deadlock on join)
        3. Wait for the processes to finish
        4. Close resources

        Args:
            timeout: Maximum time to wait for each worker (seconds).
        """
        if not self.workers:
            logger.info("No workers to shutdown")
            # Still shut down the Manager if it exists
            if not self._manager_closed:
                try:
                    self.manager.shutdown()
                    self._manager_closed = True
                except Exception as e:
                    logger.error(f"Error shutting down Manager: {e}", exc_info=True)
            return

        logger.info(f"Shutting down {len(self.workers)} worker processes")

        try:
            # 1. Signal ALL workers AT ONCE (per-worker stop_event)
            for worker_info in self.workers.values():
                worker_info.stop_event.set()

            # 2. Drain the queues BEFORE join (prevents deadlock)
            self._drain_queues()

            # 3. Wait for all processes to finish
            for server_id in list(self.workers.keys()):
                worker_info = self.workers[server_id]
                process = worker_info.process

                # stop_event was already set above for all workers
                self._terminate_process(process, server_id, timeout)

                del self.workers[server_id]

                # Close the process handle (critical on Windows)
                try:
                    process.close()
                except Exception:
                    pass

            # 4. Close IPC resources
            self._close_queues()

            logger.info("All workers shut down")

        finally:
            # ALWAYS shut down the Manager, even on errors above
            if not self._manager_closed:
                try:
                    self.manager.shutdown()
                    self._manager_closed = True
                except Exception as e:
                    logger.error(f"Error shutting down Manager: {e}", exc_info=True)

    def _drain_queues(self) -> None:
        """
        Drain the queues to prevent a deadlock during shutdown.

        Worker processes can block in queue.put() when the queue is full.
        This method reads everything out of the queues so workers can finish.
        """
        drained_count = 0

        # Drain ping_results_queue
        while True:
            try:
                self.ping_results_queue.get_nowait()
                drained_count += 1
            except QueueEmpty:
                # Queue empty - normal termination
                break
            except Exception as e:
                # Unexpected error - log and stop
                logger.warning(f"Unexpected error draining ping_results_queue: {e}")
                break

        if drained_count > 0:
            logger.debug(f"Drained {drained_count} items from the results queue")

    def _close_queues(self) -> None:
        """Close the IPC queues."""
        try:
            self.ping_results_queue.close()
        except Exception as e:
            logger.error(f"Error closing queues: {e}", exc_info=True)

    def add_server_monitoring(self, server_id: str) -> None:
        """
        Add a server to monitoring (start its worker).

        Used when:
        - Provider synchronization discovers a new enabled server
        - Reconciliation finds an enabled server without a live worker
        - Monitoring needs to be resumed for an existing enabled server

        Args:
            server_id: Server ID or, preferably, composite key in the format
                "provider:server_id".

        Raises:
            Exception: Re-raised if start_worker fails.
        """
        logger.info(f"Adding server {server_id} to monitoring")
        try:
            self.start_worker(server_id)
        except Exception as e:
            logger.error(f"Failed to add server {server_id} to monitoring: {e}")
            raise

    def remove_server_monitoring(self, server_id: str) -> None:
        """
        Remove a server from monitoring (stop its worker).

        Used when:
        - Provider synchronization removes a server
        - A provider reports a server as unmonitorable (enabled=False)

        Args:
            server_id: Server ID or, preferably, composite key in the format
                "provider:server_id".

        Raises:
            Exception: Re-raised if stop_worker fails.
        """
        logger.info(f"Removing server {server_id} from monitoring")
        try:
            self.stop_worker(server_id)
        except Exception as e:
            logger.error(f"Failed to remove server {server_id} from monitoring: {e}")
            raise

    def restart_worker(self, server_id: str, reason: str = "unknown") -> None:
        """
        Restart the worker for a server.

        Used when:
        - The server's IP address changed (a new worker with the current IP is needed)
        - A forced restart of monitoring is required

        Args:
            server_id: Server ID (composite key in the format "provider:server_id").
            reason: Reason for the restart (for logging).

        Raises:
            Exception: Re-raised if stopping or starting the worker fails.
        """
        logger.info(f"Restarting worker for {server_id}, reason: {reason}")
        try:
            # Stop the current worker
            if server_id in self.workers:
                self.stop_worker(server_id)
                logger.debug(f"Stopped old worker for {server_id}")
            else:
                logger.warning(f"No running worker found for {server_id}")

            # Start a new worker with the current data
            self.start_worker(server_id)
            logger.info(f"Successfully restarted worker for {server_id}")
        except Exception as e:
            logger.error(f"Failed to restart worker for {server_id}: {e}", exc_info=True)
            raise

    def get_worker_count(self) -> int:
        """
        Return the number of registered workers (registry size).

        NOTE: a crashed worker stays in the registry during its restart cooldown, so this
        OVERCOUNTS live workers while a crash-loop is in progress. Use get_live_worker_count()
        when you need the number of actually-running worker processes.

        Returns:
            Number of registered workers.
        """
        return len(self.workers)

    def get_live_worker_count(self) -> int:
        """
        Return the number of workers whose process is actually alive.

        Distinct from get_worker_count() (registry size): monitor_workers() deliberately
        leaves a crashed worker in the registry during its restart cooldown, so the registry
        size would mask an all-workers-down outage during a crash-loop. The health task's
        "all workers down" detector uses this live count.

        Returns:
            Number of workers with a live process.
        """
        return sum(1 for w in self.workers.values() if w.process.is_alive())

    def get_worker_info(self, server_id: str) -> WorkerInfo | None:
        """
        Return information about a worker.

        Args:
            server_id: Server ID.

        Returns:
            WorkerInfo, or None if the worker is not found.
        """
        return self.workers.get(server_id)

    def is_monitoring(self, server_id: str) -> bool:
        """
        Check whether a server has a registered worker.

        This is a registry check, not a live-process probe. A crashed worker can remain
        registered during its restart cooldown; use get_live_worker_count() when process
        liveness matters.

        Args:
            server_id: Server ID or composite key.

        Returns:
            True if a worker is registered, False otherwise.
        """
        return server_id in self.workers

    def is_manager_alive(self) -> bool:
        """
        Return whether the multiprocessing Manager SERVER PROCESS is alive.

        Checks the Manager's server process directly (a non-blocking ``is_alive()``) rather
        than doing an IPC round-trip through the shared_state proxy. A dead Manager is a
        single point of failure that silently breaks every worker's shared_state update, so
        the health task alerts on it — but the probe must NOT take ``shared_state_lock`` or
        block on a proxy call: a stalled worker/Manager holding the lock would otherwise
        wedge the very task meant to detect the fault (a hang raises no exception, so the
        caller's try/except could not recover). A process-liveness check cannot hang.

        Returns:
            bool: True if the Manager process is alive (or its state is unknown — never
                false-alarm), False if its server process has died.
        """
        proc = getattr(self.manager, "_process", None)
        if proc is None:
            # Manager not started or the attribute is unavailable: do not false-alarm.
            return True
        try:
            return bool(proc.is_alive())
        except Exception as e:
            logger.error(f"Manager liveness check failed: {e}", exc_info=True)
            return False

    def get_queue_fill_ratio(self) -> float | None:
        """
        Return the ping-results queue fill ratio in [0, 1], or None if unavailable.

        Used by the health task to alert when the queue nears capacity, which indicates
        the single consumer (ping_results_processor) is wedged or dead and workers are
        about to start dropping results. qsize() raises NotImplementedError on some
        platforms (e.g. macOS); None is returned in that case.

        Returns:
            float | None: Fill ratio, or None when qsize is unavailable.
        """
        if not self.PING_QUEUE_MAXSIZE:
            return None
        try:
            size = self.ping_results_queue.qsize()
        except Exception:
            return None
        return size / self.PING_QUEUE_MAXSIZE
