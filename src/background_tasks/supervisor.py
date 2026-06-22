"""Supervisor that restarts crashed background tasks and alerts on stalls.

asyncio does NOT restart a crashed task: a background task whose coroutine raises an
unhandled exception simply ends, its exception is stored on an un-awaited Task object
(logged only at GC), and the rest of the process keeps running as if healthy. For a
24/7 unattended monitor this is the worst failure mode — e.g. if ping_results_processor
dies, ping results stop being drained and no down/up alerts are ever sent again, yet the
bot still answers /start. This supervisor closes that gap: it watches the task set and,
when a task exits for any reason OTHER than cancellation, logs CRITICAL, alerts the
administrators, and recreates the task from its factory (bounded, then gives up + alerts).
It also compares task heartbeats against per-task budgets and alerts when a task remains
alive but stops making progress.
"""

import asyncio
import logging
import time
from collections.abc import Coroutine
from typing import Any, Callable

from aiogram import Bot

from ..bot.formatters.common import esc
from ..bot.i18n import translate
from ..bot.notifications import render_message, send_critical_error_notification
from .heartbeat import HeartbeatRegistry

logger = logging.getLogger(__name__)

# Seconds between liveness scans of the supervised task set.
SUPERVISOR_CHECK_INTERVAL = 15
# How many CONSECUTIVE crashes a single task may have before the supervisor gives up.
MAX_TASK_RESTARTS = 5
# Delay before recreating a crashed task (avoids a tight crash-restart loop).
RESTART_BACKOFF_SECONDS = 5
# A recreated task that then runs healthy for at least this long has its restart count
# reset, so MAX_TASK_RESTARTS counts CONSECUTIVE crashes rather than lifetime ones — a
# task that crashes rarely and recovers each time is never permanently abandoned.
RESTART_COUNT_RESET_SECONDS = 300

# A zero-argument factory that returns a FRESH coroutine for a supervised task.
TaskFactory = Callable[[], Coroutine[Any, Any, None]]


async def _alert_task_event(
    bot: Bot,
    admin_ids: list[int],
    name: str,
    exc: BaseException | None,
    *,
    gave_up: bool,
) -> None:
    """Notify admins that a background task exited unexpectedly.

    Never raises: a failed alert must not break the supervisor loop.

    Args:
        bot: Bot used to deliver the alert.
        admin_ids: Administrator IDs to notify.
        name: Name of the affected background task.
        exc: The exception the task raised, or None if it returned cleanly.
        gave_up: True if the restart budget is exhausted and the task is abandoned.

    Returns:
        None.
    """
    # "exc is None" means the coroutine RETURNED (a forever-running task exiting cleanly
    # is itself unexpected); a non-None exc means it raised. The body is rendered per
    # recipient: it branches on the event kind and appends the error detail when present.
    safe_name = esc(name)

    def body(language: str) -> str:
        """Render the task-event alert body in one recipient language.

        Args:
            language: Target language code for the recipient.

        Returns:
            str: Localized alert body for the task event.
        """
        if gave_up:
            message = translate(
                "alert.task_gaveup.body", language, name=safe_name, restarts=MAX_TASK_RESTARTS
            )
        elif exc is None:
            message = translate("alert.task_exited.body", language, name=safe_name)
        else:
            message = translate("alert.task_crashed.body", language, name=safe_name)
        if exc is not None:
            message += "\n\n" + translate("alert.task_error_label", language, error=esc(str(exc)))
        return message

    try:
        await send_critical_error_notification(
            bot=bot,
            admin_ids=admin_ids,
            title_key="alert.task_event.type",
            title_kwargs={"name": name},
            body=body,
        )
    except Exception as e:
        logger.error(f"Failed to alert admins about task '{name}': {e}", exc_info=True)


async def _alert_task_stalled(bot: Bot, admin_ids: list[int], name: str, age: float) -> bool:
    """Notify admins that a background task appears stalled (alive but not progressing).

    Never raises: a failed alert must not break the supervisor loop.

    Args:
        bot: Bot used to deliver the alert.
        admin_ids: Administrator IDs to notify.
        name: Name of the stalled task.
        age: Seconds since the task last made progress.

    Returns:
        bool: True if delivered to at least one administrator. The supervisor retries the
            stall alert each scan until this is True (a stalled task is not auto-restarted,
            so the alert is the only signal and must not be lost to a transient failure).
    """
    minutes = int(age // 60)
    try:
        return await send_critical_error_notification(
            bot=bot,
            admin_ids=admin_ids,
            title_key="alert.task_stalled.title",
            title_kwargs={"name": name},
            body=render_message("alert.task_stalled.body", name=name, minutes=minutes),
        )
    except Exception as e:
        logger.error(f"Failed to alert admins about stalled task '{name}': {e}", exc_info=True)
        return False


async def _scan_once(
    tasks: dict[str, asyncio.Task],
    factories: dict[str, TaskFactory],
    restart_counts: dict[str, int],
    given_up: set[str],
    started_at: dict[str, float],
    heartbeats: HeartbeatRegistry | None,
    staleness_budgets: dict[str, float] | None,
    stalled: set[str],
    stall_alerted: set[str],
    bot: Bot,
    admin_ids: list[int],
) -> None:
    """Scan the task set once, restarting crashed tasks and alerting on stalls.

    A cancelled task is treated as an intentional shutdown and ignored. A task that
    returned or raised is restarted (alert + recreate, bounded), or given up after
    MAX_TASK_RESTARTS consecutive crashes. An ALIVE task that has run healthy for
    RESTART_COUNT_RESET_SECONDS has its restart count reset; an ALIVE task whose heartbeat
    age exceeds its staleness budget is flagged stalled and alerted (once per episode).

    Args:
        tasks: name -> live asyncio.Task, MUTATED in place when a task is recreated.
        factories: name -> coroutine factory used to recreate a crashed task.
        restart_counts: name -> consecutive crash count (mutated; reset when healthy).
        given_up: set of task names that exhausted their restart budget (mutated).
        started_at: name -> monotonic time the current incarnation started (mutated).
        heartbeats: Heartbeat registry, or None to disable stall detection.
        staleness_budgets: name -> max seconds without progress before a stall alert.
        stalled: set of task names currently in a stall episode (mutated; dedups the log).
        stall_alerted: set of stalled task names whose alert was delivered (mutated; the
            alert is retried each scan until delivered, then suppressed for the episode).
        bot: Bot for admin alerts.
        admin_ids: Administrator IDs to notify.

    Returns:
        None.
    """
    now = time.monotonic()
    for name, task in list(tasks.items()):
        if name in given_up or task is None:
            continue

        if not task.done():
            # Alive: if this incarnation has been healthy long enough, reset its restart
            # budget so MAX_TASK_RESTARTS counts CONSECUTIVE crashes, not lifetime ones.
            if (
                restart_counts.get(name, 0) > 0
                and now - started_at.get(name, now) >= RESTART_COUNT_RESET_SECONDS
            ):
                restart_counts[name] = 0
                logger.info(
                    f"Background task '{name}' healthy for {RESTART_COUNT_RESET_SECONDS}s; "
                    f"restart budget reset"
                )

            # Stall detection: a task alive but not beating past its budget is wedged
            # (a blocked thread / never-returning await) — invisible to the crash-restart
            # path. Alert once per stall episode; auto-restart is deliberately NOT attempted
            # (cancelling a wedged asyncio.to_thread does not stop the underlying thread).
            if heartbeats is not None and staleness_budgets is not None:
                budget = staleness_budgets.get(name)
                age = heartbeats.age(name)
                if budget is not None and age is not None and age > budget:
                    if name not in stalled:
                        stalled.add(name)
                        logger.critical(
                            f"Background task '{name}' has not progressed for {age:.0f}s "
                            f"(budget {budget:.0f}s); it appears stalled"
                        )
                    # Retry the alert each scan until at least one admin is notified: a
                    # stalled task is NOT auto-restarted, so the alert is the only signal
                    # and must survive a transient Telegram failure on the first scan.
                    if name not in stall_alerted:
                        if await _alert_task_stalled(bot, admin_ids, name, age):
                            stall_alerted.add(name)
                elif name in stalled:
                    stalled.discard(name)
                    stall_alerted.discard(name)
                    logger.info(f"Background task '{name}' is progressing again")
            continue

        # A cancelled task is an intentional stop (shutdown), not a crash.
        if task.cancelled():
            continue

        # Safe: the task is done and not cancelled, so exception() does not raise.
        # exc is None when the coroutine RETURNED (a forever-task exiting cleanly is
        # itself unexpected); non-None when it raised.
        exc = task.exception()

        if restart_counts.get(name, 0) >= MAX_TASK_RESTARTS:
            given_up.add(name)
            logger.critical(
                f"Background task '{name}' kept failing ({restart_counts[name]} consecutive "
                f"restarts); giving up (no further restarts). Last exit: {exc!r}"
            )
            await _alert_task_event(bot, admin_ids, name, exc, gave_up=True)
            continue

        restart_counts[name] = restart_counts.get(name, 0) + 1
        if exc is None:
            logger.critical(
                f"Background task '{name}' exited cleanly but is expected to run forever "
                f"({restart_counts[name]}/{MAX_TASK_RESTARTS}); restarting after "
                f"{RESTART_BACKOFF_SECONDS}s"
            )
        else:
            logger.critical(
                f"Background task '{name}' crashed "
                f"({restart_counts[name]}/{MAX_TASK_RESTARTS}); restarting after "
                f"{RESTART_BACKOFF_SECONDS}s. Error: {exc!r}",
                exc_info=exc,
            )
        await _alert_task_event(bot, admin_ids, name, exc, gave_up=False)

        await asyncio.sleep(RESTART_BACKOFF_SECONDS)
        try:
            tasks[name] = asyncio.create_task(factories[name](), name=name)
            started_at[name] = time.monotonic()
            if heartbeats is not None:
                heartbeats.seed(name)  # avoid a false stall alert before the first beat
            stalled.discard(name)
            stall_alerted.discard(name)
            logger.info(f"Background task '{name}' restarted")
        except Exception as e:
            logger.error(f"Failed to recreate background task '{name}': {e}", exc_info=True)


async def supervise_background_tasks(
    tasks: dict[str, asyncio.Task],
    factories: dict[str, TaskFactory],
    *,
    bot: Bot,
    admin_ids: list[int],
    shutdown_event: asyncio.Event,
    heartbeats: HeartbeatRegistry | None = None,
    staleness_budgets: dict[str, float] | None = None,
    check_interval: int = SUPERVISOR_CHECK_INTERVAL,
) -> None:
    """Watch background tasks, restart unexpected exits, and alert on stalls.

    Runs concurrently with Telegram polling for the whole process lifetime. Every
    ``check_interval`` seconds it scans the task set: a task that completed for any
    reason other than cancellation is logged CRITICAL, the admins are alerted, and the
    task is recreated from its factory (up to MAX_TASK_RESTARTS, then abandoned with a
    final alert). A live task whose heartbeat exceeds its staleness budget is alerted
    without auto-restart. The ``tasks`` dict is mutated in place so the caller's shutdown
    path cancels the currently-live tasks.

    The scan body is wrapped so a transient error (e.g. a failed alert send) can never
    kill the supervisor itself — otherwise the watchdog would become another silent
    single point of failure.

    Args:
        tasks: name -> live asyncio.Task (mutated in place on restart).
        factories: name -> zero-arg coroutine factory to recreate a crashed task.
        bot: Bot used for admin alerts.
        admin_ids: Administrator IDs to notify.
        shutdown_event: set this to stop the supervisor for a graceful shutdown.
        heartbeats: Heartbeat registry shared with the tasks, or None to disable stall
            detection. Each task beats once per loop iteration.
        staleness_budgets: name -> max seconds without progress before a stall alert.
        check_interval: Seconds between liveness scans.

    Raises:
        asyncio.CancelledError: Re-raised on cancellation (graceful shutdown).

    Returns:
        None.
    """
    restart_counts: dict[str, int] = {name: 0 for name in factories}
    given_up: set[str] = set()
    stalled: set[str] = set()
    stall_alerted: set[str] = set()
    # Monotonic start time of each task's current incarnation (the initial tasks were
    # just created by the caller), used to reset the restart budget after healthy uptime.
    started_at: dict[str, float] = {name: time.monotonic() for name in factories}

    # Seed heartbeats so the brief window before each task's first beat is not a stall.
    if heartbeats is not None:
        for name in factories:
            heartbeats.seed(name)

    logger.info(f"Task supervisor started (checking every {check_interval}s)")
    try:
        while not shutdown_event.is_set():
            # Sleep until the next scan, but wake immediately on shutdown.
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=check_interval)
                break  # shutdown requested
            except asyncio.TimeoutError:
                pass  # normal: time for the next scan

            try:
                await _scan_once(
                    tasks,
                    factories,
                    restart_counts,
                    given_up,
                    started_at,
                    heartbeats,
                    staleness_budgets,
                    stalled,
                    stall_alerted,
                    bot,
                    admin_ids,
                )
            except Exception as e:
                # The supervisor must survive any per-scan error (a failed alert, a
                # transient asyncio error). Log and keep watching.
                logger.error(f"Supervisor scan error: {e}", exc_info=True)
    except asyncio.CancelledError:
        logger.info("Task supervisor stopped")
        raise
