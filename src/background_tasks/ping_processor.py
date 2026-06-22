"""Background task for draining ping results, persisting stats, and sending alerts."""

import asyncio
import logging
import time
from asyncio import Lock
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from multiprocessing import Queue
from multiprocessing.managers import DictProxy
from queue import Empty

from aiogram import Bot

from ..models import PingResult
from ..storage import ServersRepository, SqliteStatisticsRepository
from ..bot.notifications import (
    render_message,
    send_critical_error_notification,
    send_server_down_notification,
    send_server_up_notification,
)

logger = logging.getLogger(__name__)

# === STATISTICS BATCHING ===
# Global in-memory storage that accumulates ping results
ping_batch_storage: dict[str, list[PingResult]] = defaultdict(list)
last_flush_time: datetime = datetime.now()

# Lock guarding ping_batch_storage against race conditions
_batch_storage_lock = Lock()

# Running total of results across all per-server batches, kept in sync with
# ping_batch_storage under _batch_storage_lock (avoids an O(n) re-sum per result).
_batch_size: int = 0

# Notification throttling to guard against flapping. Keyed by (server_key,
# direction) so a recovery (up) alert is never throttled by a recent outage
# (down) alert and vice-versa — otherwise a genuine transition gets swallowed.
_last_notification_time: dict[tuple[str, str], float] = {}
NOTIFICATION_COOLDOWN_SECONDS = 300  # 5 minutes between notifications

# Last server status SUCCESSFULLY delivered to admins, per composite key. Drives
# down/up alerts independently of the worker's one-shot prev->current transition:
# a send that fails (and is swallowed in _broadcast_to_admins) does NOT advance this,
# so the alert is retried on the next result instead of being silently lost. Default
# "unknown"; only "offline"/"online" are stored. Pruned on server removal via forget_server().
_last_notified_status: dict[str, str] = {}

# Batching settings
BATCH_FLUSH_INTERVAL = 180  # 3 minutes, in seconds
BATCH_MAX_SIZE = 500  # Maximum records in a batch before a forced flush
BATCH_EMERGENCY_SIZE = 1000  # Emergency limit on DB errors (clears the batch)

# Debounce for the persistent-DB-failure alert (emergency batch clear): at most one alert
# per window so a sustained DB outage (disk full, corruption) does not spam admins.
DB_ALERT_COOLDOWN_SECONDS = 3600
_last_db_alert_time: float = 0.0


def _should_send_notification(server_key: str, direction: str) -> bool:
    """Check whether the cooldown has elapsed for this server AND direction.

    Args:
        server_key: Composite key of the server.
        direction: "down" or "up" - throttled independently of each other.

    Returns:
        True if a notification may be sent now, False while the cooldown is active.
    """
    now = time.time()
    last_time = _last_notification_time.get((server_key, direction))
    if last_time is None:
        return True
    return (now - last_time) >= NOTIFICATION_COOLDOWN_SECONDS


def _record_notification_sent(server_key: str, direction: str) -> None:
    """Record the time the notification was sent for this server AND direction.

    Args:
        server_key: Composite key of the server.
        direction: "down" or "up"; stored independently for per-direction cooldowns.

    Returns:
        None.
    """
    _last_notification_time[(server_key, direction)] = time.time()


def forget_server(server_key: str) -> None:
    """Drop per-server notification state for a removed server.

    The module-global notification dicts are keyed by composite key and otherwise only
    ever grow; call this when a server is removed so they do not retain entries for
    servers that no longer exist (an otherwise unbounded leak over fleet churn).

    Args:
        server_key: Composite key of the removed server.

    Returns:
        None.
    """
    _last_notified_status.pop(server_key, None)
    _last_notification_time.pop((server_key, "down"), None)
    _last_notification_time.pop((server_key, "up"), None)


def _append_to_batch_locked(result: PingResult, server_key: str) -> None:
    """
    Append a result to its per-server batch and bump the running size counter.

    IMPORTANT: the caller MUST hold `_batch_storage_lock` (asyncio.Lock is non-reentrant).

    Args:
        result: Ping result to enqueue for a later database flush.
        server_key: Composite key used to group results by server.

    Returns:
        None.
    """
    global _batch_size
    ping_batch_storage[server_key].append(result)
    _batch_size += 1


def _clear_batch_locked() -> None:
    """
    Drop all accumulated results and reset the running size counter to zero.

    IMPORTANT: the caller MUST hold `_batch_storage_lock` (asyncio.Lock is non-reentrant).

    Returns:
        None.
    """
    global _batch_size
    ping_batch_storage.clear()
    _batch_size = 0


def _drain_batch_locked() -> list[PingResult]:
    """
    Collect all accumulated results, clear the batch, and update last_flush_time.

    IMPORTANT: the caller MUST hold `_batch_storage_lock` (asyncio.Lock is non-reentrant).
    Returns an empty list if the batch has no results.

    Returns:
        List of drained ping results, or an empty list when nothing was buffered.
    """
    global last_flush_time

    if not ping_batch_storage:
        return []

    all_results = [result for batch in ping_batch_storage.values() for result in batch]
    if not all_results:
        return []

    _clear_batch_locked()
    last_flush_time = datetime.now()
    return all_results


async def _periodic_batch_flush(
    stats_repo: SqliteStatisticsRepository,
    bot: Bot | None = None,
    admin_ids: list[int] | None = None,
    interval: int = 30,
) -> None:
    """
    Background task that periodically checks the batch timeout.

    Every ``interval`` seconds it checks whether the batch should be flushed due
    to the timeout. Runs alongside the main results processor.

    Args:
        stats_repo: SQLite statistics repository
        bot: Bot used to alert admins on a persistent DB-write failure (optional)
        admin_ids: Administrator IDs to notify (optional)
        interval: Check interval in seconds (default 30)

    Returns:
        None.

    Raises:
        asyncio.CancelledError: Re-raised when the task is cancelled.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            await _check_and_flush_batch(stats_repo, bot, admin_ids)
    except asyncio.CancelledError:
        raise


async def ping_results_processor(
    bot: Bot,
    ping_results_queue: Queue,
    shared_state: DictProxy,
    admin_ids: list[int],
    servers_repo: ServersRepository,
    stats_repo: SqliteStatisticsRepository,
    heartbeat: Callable[[], None] = lambda: None,
) -> None:
    """
    Background task for processing ping results.

    Reads results from the IPC queue (non-blocking via asyncio.to_thread),
    accumulates statistics in batches (flushed every 3 minutes or at 500 records),
    tracks server status changes, and sends delivery-confirmed notifications to
    administrators. Down/up alert state advances only after at least one successful
    Telegram delivery, so failed sends are retried on later ping results.

    Args:
        bot: aiogram Bot instance used to send messages
        ping_results_queue: Queue with results from worker processes
        shared_state: Shared dictionary holding server state
        admin_ids: List of administrator IDs to notify
        servers_repo: Servers repository
        stats_repo: SQLite statistics repository
        heartbeat: Called once per loop iteration so the supervisor can detect a stall
            (the task alive but wedged). Defaults to a no-op for standalone use/tests.

    Returns:
        None.

    Raises:
        asyncio.CancelledError: Re-raised after shutdown flush and cleanup.
        Exception: Re-raised after a critical processor failure and best-effort flush.
    """
    # Start the background task that periodically checks the timeout
    periodic_flush_task = asyncio.create_task(
        _periodic_batch_flush(stats_repo, bot=bot, admin_ids=admin_ids, interval=30)
    )

    try:
        while True:
            heartbeat()  # progress beat at the top of every loop iteration
            # Use asyncio.to_thread for non-blocking reads from the Queue
            # CRITICAL: Queue.get() is a blocking operation, never call it directly!
            try:
                result_dict = await asyncio.to_thread(
                    ping_results_queue.get,
                    True,  # block
                    1.0,  # timeout (seconds)
                )
            except Empty:
                # Queue is empty - wait for new results
                # The timeout check is handled by the background task
                await asyncio.sleep(0.1)
                continue

            # Parse the result into a PingResult model
            try:
                result = PingResult(**result_dict)
            except (TypeError, ValueError, KeyError) as e:
                logger.error(f"Failed to parse ping result: {e}", exc_info=True)
                continue

            # Composite key to prevent ID collisions across providers
            server_key = f"{result.provider_type}:{result.server_id}"

            # Process this result defensively: a failure anywhere below (DB flush,
            # repo lookup, or a notification send raising a non-Telegram exception)
            # must NEVER kill the processor — it is the only task that turns pings
            # into alerts/stats, so its death silently stops all monitoring.
            try:
                # Append the result to the batch (IN MEMORY, not written to the DB immediately)
                # Use the Lock to guard against race conditions
                async with _batch_storage_lock:
                    _append_to_batch_locked(result, server_key)

                # Check whether the batch needs to be flushed
                await _check_and_flush_batch(stats_repo, bot, admin_ids)

                # Look up the server info by composite key
                # IMPORTANT: result.provider_type holds the provider_alias (e.g. "hetzner_prod"),
                # not the provider type ("hetzner"). Use get_by_composite_key for a correct lookup.
                server = servers_repo.get_by_composite_key(server_key)

                if not server:
                    logger.warning(f"Server not found by composite key: {server_key}")
                    continue

                # Decide notifications from the worker-computed current_status, but gate
                # delivery on a processor-side "last notified" status (advanced only on a
                # confirmed delivery). A send that fails is therefore RETRIED on the next
                # result instead of being silently consumed by the worker's one-shot
                # prev->current transition. The per-direction cooldown still throttles
                # flapping; both the cooldown and the notified-status advance only on success.
                current_status = result.current_status
                last_notified = _last_notified_status.get(server_key, "unknown")

                if current_status == "offline" and last_notified != "offline":
                    # Server is down and admins have not yet been told it is down.
                    if _should_send_notification(server_key, "down"):
                        delivered = await send_server_down_notification(
                            bot=bot,
                            admin_ids=admin_ids,
                            server_name=server.name,
                            server_ip=server.ip,
                            error=result.error,
                        )
                        if delivered:
                            _record_notification_sent(server_key, "down")
                            _last_notified_status[server_key] = "offline"
                        else:
                            logger.warning(
                                f"Down alert for {server_key} not delivered to any admin; "
                                f"will retry on the next result"
                            )
                    else:
                        logger.debug(f"Skipping offline notification for {server_key} (cooldown)")
                elif current_status == "online" and last_notified == "offline":
                    # Server recovered and admins were previously told it was down.
                    if _should_send_notification(server_key, "up"):
                        delivered = await send_server_up_notification(
                            bot=bot,
                            admin_ids=admin_ids,
                            server_name=server.name,
                            server_ip=server.ip,
                            response_time_ms=result.response_time_ms,
                        )
                        if delivered:
                            _record_notification_sent(server_key, "up")
                            _last_notified_status[server_key] = "online"
                        else:
                            logger.warning(
                                f"Recovery alert for {server_key} not delivered to any admin; "
                                f"will retry on the next result"
                            )
                    else:
                        logger.debug(f"Skipping online notification for {server_key} (cooldown)")
            except Exception as e:
                # Log and move on to the next result; one bad result/send can never
                # terminate the result-draining loop.
                logger.error(
                    f"Failed to process ping result for {server_key}: {e}", exc_info=True
                )
                continue

    except asyncio.CancelledError:
        # Stop the background task
        periodic_flush_task.cancel()
        try:
            await periodic_flush_task
        except asyncio.CancelledError:
            pass
        # Flush the remaining batch before exiting
        try:
            await _flush_batch(stats_repo, bot, admin_ids)
        except Exception as e:
            logger.error(f"Failed to flush batch during shutdown: {e}")
        finally:
            # CRITICAL: clear the batch no matter what (prevent a memory leak)
            async with _batch_storage_lock:
                _clear_batch_locked()
        raise
    except Exception as e:
        logger.error(f"Critical error in ping results processor: {e}", exc_info=True)
        # Stop the background task
        periodic_flush_task.cancel()
        try:
            await periodic_flush_task
        except asyncio.CancelledError:
            pass
        # Flush the batch even on a critical error
        try:
            await _flush_batch(stats_repo, bot, admin_ids)
        except Exception as flush_error:
            logger.error(f"Failed to flush batch during error handling: {flush_error}")
        finally:
            # CRITICAL: clear the batch to prevent a memory leak
            async with _batch_storage_lock:
                _clear_batch_locked()
        raise


async def _check_and_flush_batch(
    stats_repo: SqliteStatisticsRepository,
    bot: Bot | None = None,
    admin_ids: list[int] | None = None,
) -> None:
    """
    Atomically check the flush conditions and flush the batch if needed.

    Flush conditions:
    - BATCH_FLUSH_INTERVAL seconds have passed since the last flush
    - OR the batch has accumulated BATCH_MAX_SIZE records

    NOTE: the check and the flush run atomically under a single Lock to prevent
    a race condition where two tasks decide to flush the batch simultaneously.

    Args:
        stats_repo: SQLite statistics repository
        bot: Bot used to alert admins on a persistent DB-write failure (optional)
        admin_ids: Administrator IDs to notify (optional)

    Returns:
        None.
    """
    # Atomically check the conditions AND drain the batch under one lock
    async with _batch_storage_lock:
        time_since_flush = (datetime.now() - last_flush_time).total_seconds()
        total_batch_size = _batch_size

        should_flush = (
            time_since_flush >= BATCH_FLUSH_INTERVAL or total_batch_size >= BATCH_MAX_SIZE
        )

        if not should_flush:
            return

        all_results = _drain_batch_locked()

    if not all_results:
        return

    # Write to the DB WITHOUT the lock (long-running operation)
    await _write_batch_to_db(stats_repo, all_results, bot, admin_ids)


async def _flush_batch(
    stats_repo: SqliteStatisticsRepository,
    bot: Bot | None = None,
    admin_ids: list[int] | None = None,
) -> None:
    """
    Forcibly flush the accumulated batch to the DB.

    Used at shutdown or for a forced flush. For a regular flush, use
    _check_and_flush_batch(). If the DB write fails, the drained results are re-queued
    by _write_batch_to_db() and may later be dropped only by the emergency cap.

    Args:
        stats_repo: SQLite statistics repository
        bot: Bot used to alert admins on a persistent DB-write failure (optional)
        admin_ids: Administrator IDs to notify (optional)

    Returns:
        None.
    """
    # Drain the batch under the lock
    async with _batch_storage_lock:
        all_results = _drain_batch_locked()

    if not all_results:
        return

    # Write to the DB WITHOUT the lock (long-running operation)
    await _write_batch_to_db(stats_repo, all_results, bot, admin_ids)


async def _alert_db_failure(bot: Bot, admin_ids: list[int], dropped: int) -> None:
    """Send a debounced critical alert that statistics DB writes are failing.

    Triggered by the emergency batch clear (records dropped after repeated DB errors).
    Debounced to at most one alert per DB_ALERT_COOLDOWN_SECONDS so a sustained outage
    does not spam admins. Never raises.

    Args:
        bot: Bot used to deliver the alert.
        admin_ids: Administrator IDs to notify.
        dropped: Number of statistics records discarded by the emergency clear.

    Returns:
        None.
    """
    global _last_db_alert_time
    now = time.time()
    if now - _last_db_alert_time < DB_ALERT_COOLDOWN_SECONDS:
        return
    try:
        delivered = await send_critical_error_notification(
            bot=bot,
            admin_ids=admin_ids,
            title_key="alert.db_failure.title",
            body=render_message("alert.db_failure.body", dropped=dropped),
        )
    except Exception as e:
        logger.error(f"Failed to send DB-failure alert: {e}", exc_info=True)
        return
    # Start the cooldown only after a confirmed delivery, so a failed send is retried on
    # the next emergency clear instead of being suppressed for the whole window.
    if delivered:
        _last_db_alert_time = now


async def _write_batch_to_db(
    stats_repo: SqliteStatisticsRepository,
    all_results: list[PingResult],
    bot: Bot | None = None,
    admin_ids: list[int] | None = None,
) -> None:
    """
    Write a batch of results to the DB with error handling.

    On a transient DB error the batch is re-queued for the next flush; if errors persist
    and the batch exceeds BATCH_EMERGENCY_SIZE it is dropped (to bound memory) and the
    admins are alerted (debounced) so a failing DB is not silent.

    Args:
        stats_repo: SQLite statistics repository
        all_results: List of results to write
        bot: Bot used to alert admins on the emergency drop (optional)
        admin_ids: Administrator IDs to notify (optional)

    Returns:
        None.
    """
    global last_flush_time

    try:
        # add_ping_batch writes inside one transaction (rolled back on error), so
        # re-queuing the whole batch below cannot double-count.
        await asyncio.to_thread(stats_repo.add_ping_batch, all_results)
        return
    except Exception as e:
        logger.error(f"Failed to flush batch to DB: {e}", exc_info=True)

    emergency_dropped = 0
    async with _batch_storage_lock:
        # Re-queue the failed results so the next flush retries them, instead
        # of silently losing up to BATCH_MAX_SIZE pings on a transient DB error.
        for result in all_results:
            _append_to_batch_locked(result, f"{result.provider_type}:{result.server_id}")

        # CRITICAL: if the batch keeps growing while DB errors persist, drop it
        # to prevent an unbounded memory leak.
        if _batch_size > BATCH_EMERGENCY_SIZE:
            emergency_dropped = _batch_size
            logger.warning(
                f"Emergency batch clear triggered: {_batch_size} > {BATCH_EMERGENCY_SIZE} "
                f"(repeated DB errors detected)"
            )
            _clear_batch_locked()
            last_flush_time = datetime.now()

    # Alert admins (debounced) OUTSIDE the lock, only when records were actually dropped.
    if emergency_dropped and bot is not None and admin_ids:
        await _alert_db_failure(bot, admin_ids, emergency_dropped)
