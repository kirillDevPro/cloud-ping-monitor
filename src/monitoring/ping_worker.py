"""Worker process for monitoring a single server."""

import logging
import time
from datetime import datetime
from multiprocessing import Queue
from multiprocessing.managers import DictProxy
from multiprocessing.synchronize import Event as EventType
from queue import Full
from typing import Literal

import ping3

from ..models import PingResult, PingStatus
from ..utils.logger import setup_worker_logger

# Type alias for a server's status value
ServerStatusType = Literal["online", "offline", "unknown"]

# Base module-level logger
logger = logging.getLogger(__name__)


def perform_ping(
    ip: str, timeout: int, attempts: int
) -> tuple[PingStatus, float | None, int, str | None]:
    """
    Perform an ICMP ping with the given number of attempts.

    Args:
        ip: IP address to ping.
        timeout: Timeout per attempt, in seconds.
        attempts: Number of ping attempts.

    Returns:
        Tuple of (status, avg_response_time_ms, failed_attempts, error_message).
        avg_response_time_ms is the mean of successful attempts, or None when
        every attempt failed.
    """
    response_times = []
    failed_count = 0
    last_error: str | None = None

    for attempt in range(attempts):
        try:
            # Send a single ping
            response_time = ping3.ping(ip, timeout=timeout, unit="ms")

            if response_time is None:
                # Timed out or unreachable
                failed_count += 1
                last_error = "Timeout"
                logger.debug(f"Ping to {ip}: TIMEOUT (attempt {attempt + 1}/{attempts})")
            elif response_time is False:
                # Ping execution error
                failed_count += 1
                last_error = "Ping failed"
                logger.debug(f"Ping to {ip}: FAILED (attempt {attempt + 1}/{attempts})")
            else:
                # Success
                response_times.append(float(response_time))
                logger.debug(
                    f"Ping to {ip}: SUCCESS {response_time:.2f}ms "
                    f"(attempt {attempt + 1}/{attempts})"
                )

        except Exception as e:
            # Unexpected error while pinging
            failed_count += 1
            last_error = f"Exception: {str(e)}"
            logger.debug(f"Ping to {ip}: ERROR {str(e)} " f"(attempt {attempt + 1}/{attempts})")

        # Brief pause between attempts (skipped after the last one)
        if attempt < attempts - 1:
            time.sleep(1)

    # Derive the overall result
    if response_times:
        # At least one attempt succeeded
        avg_response = sum(response_times) / len(response_times)
        return PingStatus.SUCCESS, avg_response, failed_count, None
    elif failed_count == attempts:
        # Every attempt timed out or failed
        if last_error == "Timeout":
            return PingStatus.TIMEOUT, None, failed_count, "All attempts timed out"
        else:
            return PingStatus.FAILED, None, failed_count, last_error
    else:
        # Unexpected case (no successes and not all attempts failed)
        return PingStatus.FAILED, None, failed_count, "Unknown error"


def ping_worker_function(
    server_id: str,
    server_ip: str,
    provider_alias: str,
    ping_results_queue: Queue,
    shared_state: DictProxy,
    shared_state_lock,  # multiprocessing.Lock
    stop_event: EventType,
    ping_interval: int,
    ping_timeout: int,
    ping_attempts: int,
) -> None:
    """
    Main entry point of the worker process that monitors a single server.

    The worker performs an ICMP ping at the configured interval and pushes the
    results to the main process through a queue, maintaining the server's
    current status in the shared state and exiting cleanly when stop_event is set.

    Args:
        server_id: Server identifier.
        server_ip: Server IP address.
        provider_alias: Provider alias (e.g. "hetzner_prod", "vultr_main").
            Used to build the composite key: f"{provider_alias}:{server_id}".
        ping_results_queue: Queue for sending ping results to the main process.
        shared_state: Shared state mapping (Manager.dict / DictProxy).
        shared_state_lock: Lock guarding read-modify-write operations on shared_state.
        stop_event: Event signalling a graceful shutdown.
        ping_interval: Interval between pings, in seconds.
        ping_timeout: Timeout per attempt, in seconds.
        ping_attempts: Number of ICMP echo attempts per ping cycle (passed to
            perform_ping(attempts=...)). The same value is reused as the offline
            threshold: the server is marked offline once consecutive_failures
            reaches it.

    Raises:
        Exception: Re-raised when the worker crashes outside handled ping/send errors.
    """
    # Set up dedicated logging for this worker.
    # IMPORTANT: each worker writes to its own log file
    # (active file logs/{alias}/worker_{server_id}.log, rotated to .log.YYYY-MM-DD).
    worker_logger = setup_worker_logger(
        server_id=server_id, provider_alias=provider_alias, log_level="DEBUG"
    )

    worker_logger.info(
        f"Worker started for server {server_id} ({server_ip}), "
        f"interval={ping_interval}s, timeout={ping_timeout}s, attempts={ping_attempts}"
    )

    # Composite key for shared_state (avoids server ID clashes across providers)
    server_key = f"{provider_alias}:{server_id}"

    # Status last SUCCESSFULLY delivered to the main process. The result's
    # previous_status is taken from this (not from shared_state) so a result
    # dropped on a full queue does not erase the transition: the next cycle still
    # reports prev != current and the notification fires.
    last_sent_status: ServerStatusType = "unknown"

    # Initialize / inherit this server's shared_state entry (atomically). A
    # crash-restart via monitor_workers does NOT delete the entry, so preserving it
    # keeps the transition baseline (last status + consecutive_failures) — resetting
    # to "unknown" would duplicate a "down" alert or miss a "recovery". A genuinely
    # new server (or one restarted via stop_worker, which deletes the entry) starts
    # fresh at "unknown".
    with shared_state_lock:
        existing = shared_state.get(server_key)
        if existing is None:
            shared_state[server_key] = {
                "status": "unknown",
                "last_ping_time": None,
                "response_time_ms": None,
                "consecutive_failures": 0,
            }
        else:
            status = existing.get("status", "unknown")
            if status in ("online", "offline", "unknown"):
                last_sent_status = status

    try:
        # Worker main loop
        while not stop_event.is_set():
            # Run the ping
            ping_start_time = datetime.now()
            worker_logger.debug(f"Starting ping to {server_ip}")

            status, response_time_ms, failed_count, error = perform_ping(
                ip=server_ip, timeout=ping_timeout, attempts=ping_attempts
            )

            # Compute consecutive_failures and current_status (atomically under the lock).
            # Initialize the variables with safe defaults.
            consecutive_failures = 0
            current_status: ServerStatusType = "unknown"

            try:
                # CRITICAL: the read-modify-write on shared_state must be atomic
                with shared_state_lock:
                    # Read the previous state to advance the failure counter
                    prev_state = shared_state.get(server_key, {})

                    # Update the consecutive failure counter
                    if status == PingStatus.SUCCESS:
                        consecutive_failures = 0
                    else:
                        consecutive_failures = prev_state.get("consecutive_failures", 0) + 1

                    # Derive the status from the failure count
                    if status == PingStatus.SUCCESS:
                        current_status = "online"
                    elif consecutive_failures >= ping_attempts:
                        current_status = "offline"
                    else:
                        current_status = "unknown"

                    # Write the new state back
                    shared_state[server_key] = {
                        "status": current_status,
                        "last_ping_time": ping_start_time.isoformat(),
                        "response_time_ms": response_time_ms,
                        "consecutive_failures": consecutive_failures,
                    }
            except Exception as e:
                worker_logger.error(f"Failed to update shared_state: {e}", exc_info=True)
                # The variables were already initialized with defaults above

            # Build the result with the computed consecutive_failures, current_status
            # and previous_status.
            # IMPORTANT: PingResult.provider_type stores the provider alias (not the type!)
            result = PingResult(
                server_id=server_id,
                provider_type=provider_alias,
                timestamp=ping_start_time,
                status=status,
                response_time_ms=response_time_ms,
                packet_loss=((failed_count / ping_attempts) * 100) if failed_count > 0 else 0.0,
                error=error,
                consecutive_failures=consecutive_failures,
                current_status=current_status,
                previous_status=last_sent_status,
            )

            # Send the result to the main process, retrying when the queue is full
            max_queue_retries = 3
            for attempt in range(max_queue_retries):
                try:
                    ping_results_queue.put(result.model_dump(), timeout=5.0)
                    worker_logger.debug(
                        f"Result sent: {status.value}, response_time={response_time_ms}ms"
                    )
                    # Mark this status as delivered so the next cycle's previous_status
                    # reflects what the main process actually received.
                    last_sent_status = current_status
                    break  # Sent successfully
                except Full:
                    if attempt < max_queue_retries - 1:
                        delay = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
                        worker_logger.warning(
                            f"Queue full, retry {attempt + 1}/{max_queue_retries} in {delay}s"
                        )
                        # Sleep in 1s steps so a shutdown during backoff is noticed.
                        for _ in range(delay):
                            if stop_event.is_set():
                                break
                            time.sleep(1)
                        if stop_event.is_set():
                            break
                    else:
                        worker_logger.error(
                            f"Queue full after {max_queue_retries} retries, result DROPPED! "
                            f"server={server_id}, status={status.value}"
                        )
                except Exception as e:
                    worker_logger.error(f"Failed to send result to queue: {e}", exc_info=True)
                    break  # Unknown error - do not retry

            # Wait ping_interval seconds, checking stop_event every second.
            # CRITICAL: frequent stop_event checks enable a fast graceful shutdown.
            for _ in range(ping_interval):
                if stop_event.is_set():
                    break
                time.sleep(1)

    except Exception as e:
        worker_logger.error(f"Worker crashed with exception: {e}", exc_info=True)
        raise
    finally:
        worker_logger.info(f"Worker stopped for server {server_id}")
