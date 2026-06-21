"""Background task: worker health, abandonment alerts, reconciliation, and subsystem health."""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from aiogram import Bot

from ..bot.notifications import send_critical_error_notification

if TYPE_CHECKING:
    from ..monitoring.ping_manager import PingManager
    from ..storage import ServersRepository

logger = logging.getLogger(__name__)

# How often to poll worker health and reconcile monitoring, in seconds.
WORKERS_HEALTH_INTERVAL = 30


async def _safe_critical_alert(
    bot: Bot, admin_ids: list[int], error_type: str, error_message: str
) -> bool:
    """Send a critical admin alert, swallowing any failure (must never break the loop).

    Args:
        bot: Bot used to deliver the alert.
        admin_ids: Administrator IDs to notify.
        error_type: Short alert category shown in the header.
        error_message: Alert body.

    Returns:
        bool: True if delivered to at least one administrator, False otherwise. Debounced
            callers set their "alerted" flag only on True so a failed send is retried.
    """
    try:
        return await send_critical_error_notification(
            bot=bot, admin_ids=admin_ids, error_type=error_type, error_message=error_message
        )
    except Exception as e:
        logger.error(f"Failed to send subsystem alert '{error_type}': {e}", exc_info=True)
        return False


async def _alert_worker_abandoned(
    bot: Bot,
    admin_ids: list[int],
    server_key: str,
    servers_repo: "ServersRepository",
) -> None:
    """Notify admins that a server's worker exhausted its restart budget.

    The alert send itself is best-effort through _safe_critical_alert(), but repository
    lookup errors are not swallowed here.

    Args:
        bot: Bot used to deliver the alert.
        admin_ids: Administrator IDs to notify.
        server_key: Composite key of the abandoned server.
        servers_repo: Repository used to resolve a human-readable server label.
    """
    server = servers_repo.get_by_composite_key(server_key)
    label = f"{server.name} ({server.ip})" if server else server_key
    await _safe_critical_alert(
        bot,
        admin_ids,
        "Мониторинг сервера остановлен",
        f"Воркер мониторинга сервера {label} несколько раз подряд аварийно завершался "
        f"и был остановлен. Сервер временно не мониторится — будет автоматическая "
        f"повторная попытка позже. Проверьте сервер и сеть.",
    )


class _SubsystemHealthMonitor:
    """Debounced detection of monitoring-subsystem failures.

    Holds the cross-cycle debounce state for three conditions the per-worker restart loop
    cannot surface: a dead Manager process, ALL workers down while servers are expected,
    and the results queue near capacity (a wedged/dead consumer). Each fires at most one
    admin alert per episode and re-arms once the condition clears.
    """

    # Consecutive cycles a condition must hold before alerting (debounce against blips).
    ALL_WORKERS_DOWN_CYCLES = 3
    QUEUE_FULL_CYCLES = 3
    # Queue fill ratio considered "near capacity".
    QUEUE_FILL_THRESHOLD = 0.8

    def __init__(self) -> None:
        """Initialize all debounce counters/flags to a healthy state."""
        self._manager_dead_alerted = False
        self._zero_workers_count = 0
        self._zero_workers_alerted = False
        self._queue_full_count = 0
        self._queue_full_alerted = False

    async def check(
        self, ping_manager: "PingManager", enabled_count: int, bot: Bot, admin_ids: list[int]
    ) -> None:
        """Run all three subsystem-health checks and alert (debounced) on any failure.

        Args:
            ping_manager: PingManager exposing the liveness / worker-count / queue probes.
            enabled_count: Number of enabled (expected-to-be-monitored) servers.
            bot: Bot used for admin alerts.
            admin_ids: Administrator IDs to notify.
        """
        # Each alert sets its debounce flag ONLY after a confirmed delivery, so a failed
        # send is retried on the next cycle instead of being silently suppressed for the
        # whole episode.

        # A4a: the Manager process (backs shared_state) is a single point of failure.
        if not ping_manager.is_manager_alive():
            if not self._manager_dead_alerted:
                if await _safe_critical_alert(
                    bot,
                    admin_ids,
                    "Ядро мониторинга недоступно",
                    "Процесс Manager (хранит статусы серверов) не отвечает. Обновление "
                    "статусов и часть мониторинга нарушены. Вероятно, потребуется "
                    "перезапуск бота.",
                ):
                    self._manager_dead_alerted = True
        else:
            self._manager_dead_alerted = False

        # A4b: zero LIVE workers while servers are expected to be monitored. Uses the live
        # process count, not the registry size (a crashed worker lingers in the registry
        # during its restart cooldown, which would mask an all-down outage).
        live = ping_manager.get_live_worker_count()
        if enabled_count > 0 and live == 0:
            self._zero_workers_count += 1
            if (
                self._zero_workers_count >= self.ALL_WORKERS_DOWN_CYCLES
                and not self._zero_workers_alerted
            ):
                if await _safe_critical_alert(
                    bot,
                    admin_ids,
                    "Мониторинг полностью остановлен",
                    f"Ни один из {enabled_count} серверов не мониторится (нет живых "
                    f"воркеров) уже несколько проверок подряд. Проверьте бота и логи.",
                ):
                    self._zero_workers_alerted = True
        else:
            self._zero_workers_count = 0
            self._zero_workers_alerted = False

        # U1: results queue near capacity -> the single consumer is likely wedged/dead.
        ratio = ping_manager.get_queue_fill_ratio()
        if ratio is not None and ratio >= self.QUEUE_FILL_THRESHOLD:
            self._queue_full_count += 1
            if self._queue_full_count >= self.QUEUE_FULL_CYCLES and not self._queue_full_alerted:
                if await _safe_critical_alert(
                    bot,
                    admin_ids,
                    "Очередь результатов переполняется",
                    f"Очередь результатов пинга заполнена на ~{ratio * 100:.0f}% уже "
                    f"несколько проверок подряд — обработчик результатов, похоже, завис "
                    f"или умер. Статистика и уведомления могут теряться.",
                ):
                    self._queue_full_alerted = True
        else:
            self._queue_full_count = 0
            self._queue_full_alerted = False


async def workers_health_task(
    ping_manager: "PingManager",
    servers_repo: "ServersRepository",
    bot: Bot,
    admin_ids: list[int],
    interval_seconds: int = WORKERS_HEALTH_INTERVAL,
    heartbeat: Callable[[], None] = lambda: None,
) -> None:
    """
    Periodically restart crashed workers, alert on abandonment, reconcile, and watch
    the monitoring subsystem.

    Each cycle:
    1. PingManager.monitor_workers() restarts dead workers (bounded by
       MAX_RESTART_ATTEMPTS / RESTART_COOLDOWN, with the budget reset after healthy
       uptime) and returns workers it gave up on this cycle.
    2. PingManager.reconcile_monitoring() (re)starts monitoring for any enabled server
       with no live worker — healing boot-failed workers and, after a long backoff,
       abandoned ones — so a server never silently drops out of monitoring permanently.
    3. Each abandoned worker triggers a critical admin alert.
    4. _SubsystemHealthMonitor checks for subsystem-wide failures the per-worker loop
       cannot surface (a dead Manager, all workers down, a near-full results queue) and
       alerts (debounced).

    monitor_workers() and reconcile_monitoring() mutate ping_manager.workers; they are
    invoked here as plain sync calls on the asyncio event-loop thread (the only awaits in
    this cycle are admin alerts) so they never race with the other workers-dict mutators
    (servers_sync, add/remove_server_monitoring) that also run on the loop.

    Args:
        ping_manager: The PingManager whose workers are health-checked and reconciled.
        servers_repo: Repository providing the enabled-server set and server labels.
        bot: Bot used for admin alerts.
        admin_ids: Administrator IDs to notify.
        interval_seconds: Seconds between checks. The first check waits one full
            interval before running.
        heartbeat: Called once per loop iteration so the supervisor can detect a stall.
            Defaults to a no-op for standalone use/tests.

    Raises:
        asyncio.CancelledError: Re-raised when the task is cancelled (on shutdown).
    """
    logger.info(f"Worker health task started (every {interval_seconds}s)")
    health_monitor = _SubsystemHealthMonitor()
    try:
        while True:
            heartbeat()  # progress beat at the top of every loop iteration
            await asyncio.sleep(interval_seconds)
            try:
                abandoned = ping_manager.monitor_workers()

                # Reconcile FIRST (sync, no I/O): self-healing of unmonitored servers must
                # not wait behind Telegram alert delivery, which can flood-control sleep.
                # Otherwise a burst of abandonments (or a slow Telegram API) would stall
                # recovery of every other unmonitored server during an incident.
                enabled_servers = [s for s in servers_repo.get_all() if s.enabled]
                ping_manager.reconcile_monitoring(enabled_servers)

                for server_key in abandoned:
                    await _alert_worker_abandoned(bot, admin_ids, server_key, servers_repo)

                # Detect monitoring-subsystem failures the per-worker loop cannot surface.
                await health_monitor.check(ping_manager, len(enabled_servers), bot, admin_ids)
            except Exception as e:
                logger.error(f"Worker health check failed: {e}", exc_info=True)
    except asyncio.CancelledError:
        logger.info("Worker health task stopped")
        raise
