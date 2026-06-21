"""Centralized logging system for the application."""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


# Logging constants
# Compact format: time | level | message (without module paths)
LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Time-based log rotation parameters
LOG_RETENTION_DAYS = (
    7  # Keep logs for 7 days (TimedRotatingFileHandler deletes old ones automatically)
)


def get_log_level(level_str: str) -> int:
    """
    Convert a logging level string into a logging constant.

    Args:
        level_str: Level string ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    Returns:
        int: Logging level constant

    Examples:
        >>> get_log_level("INFO")
        20
        >>> get_log_level("debug")
        10
    """
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    level_upper = level_str.upper()
    return level_map.get(level_upper, logging.INFO)


def setup_main_logger(
    log_level: str = "INFO", log_file: Path | None = None, console_output: bool = True
) -> logging.Logger:
    """
    Configure logging for the main process.

    Creates a root logger with:
    - TimedRotatingFileHandler writing to logs/main.log, rotated at midnight to
      logs/main.log.YYYY-MM-DD (the active file is always logs/main.log)
    - StreamHandler for console output (optional)
    - Formatting with a timestamp and log level
    - Automatic rotation at midnight, retaining logs for 7 days

    Args:
        log_level: Logging level ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        log_file: Path to the base log file without a date (default: logs/main.log)
        console_output: Whether to also emit logs to the console

    Returns:
        logging.Logger: The configured root logger

    Example:
        >>> logger = setup_main_logger(log_level="INFO")
        >>> logger.info("Application started")
    """
    # Get the root logger
    root_logger = logging.getLogger()

    # Clear existing handlers (if any)
    root_logger.handlers.clear()

    # Set the logging level
    level = get_log_level(log_level)
    root_logger.setLevel(level)

    # Create the formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # 1. Configure the file handler with time-based rotation
    if log_file is None:
        # Resolve the logs path relative to the project root
        project_root = Path(__file__).parent.parent.parent
        logs_dir = project_root / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / "main.log"
    else:
        # Make sure the directory exists
        log_file.parent.mkdir(parents=True, exist_ok=True)

    # TimedRotatingFileHandler automatically appends the date in YYYY-MM-DD format
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",  # Rotate at midnight
        interval=1,  # Every day
        backupCount=LOG_RETENTION_DAYS,  # Keep 7 days
        encoding="utf-8",
        utc=False,  # Use local time
    )
    # Set the date suffix in YYYY-MM-DD format
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 2. Configure the console handler (optional)
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Log the initialization (debug - not needed in production)
    root_logger.debug(
        f"Logger: level={log_level}, file={log_file.name}, retention={LOG_RETENTION_DAYS}d"
    )

    return root_logger


def setup_worker_logger(
    server_id: str,
    provider_alias: str,
    log_level: str = "DEBUG",
    log_file: Path | None = None,
) -> logging.Logger:
    """
    Configure logging for a worker process.

    Creates a dedicated logger for each worker process with:
    - Its own active log file in the provider subfolder
      (logs/{alias}/worker_{server_id}.log), rotated to .log.YYYY-MM-DD backups
    - TimedRotatingFileHandler that rotates at midnight
    - A more verbose logging level (DEBUG by default)
    - Automatic 7-day log retention

    CRITICAL: This function must be called INSIDE the worker process,
    not in the main process! Otherwise the logs will get mixed together.

    Args:
        server_id: Server ID used to name the log file
        provider_alias: Provider alias (hetzner_prod, vultr_main) used to group logs
        log_level: Logging level (DEBUG by default for workers)
        log_file: Path to the log file (if None, generated automatically)

    Returns:
        logging.Logger: The configured logger for the worker process

    Example:
        >>> # Inside the worker process:
        >>> logger = setup_worker_logger(server_id="abc123", provider_alias="vultr_main")
        >>> logger.debug("Ping started for server abc123")
    """
    # Create a logger with a unique name (using the composite key)
    logger_name = f"worker.{provider_alias}.{server_id}"
    worker_logger = logging.getLogger(logger_name)

    # Clear existing handlers (if any)
    worker_logger.handlers.clear()

    # Set the logging level
    level = get_log_level(log_level)
    worker_logger.setLevel(level)

    # Do not propagate logs to the root logger (avoid duplication)
    worker_logger.propagate = False

    # Create the formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Configure the file handler with time-based rotation
    if log_file is None:
        # Resolve the logs path relative to the project root
        project_root = Path(__file__).parent.parent.parent
        logs_dir = project_root / "logs"

        # Create the provider subfolder (logs/hetzner_prod/, logs/vultr_main/)
        provider_logs_dir = logs_dir / provider_alias.lower()
        provider_logs_dir.mkdir(parents=True, exist_ok=True)

        log_file = provider_logs_dir / f"worker_{server_id}.log"
    else:
        # Make sure the directory exists
        log_file.parent.mkdir(parents=True, exist_ok=True)

    # TimedRotatingFileHandler automatically appends the date in YYYY-MM-DD format
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",  # Rotate at midnight
        interval=1,  # Every day
        backupCount=LOG_RETENTION_DAYS,  # Keep 7 days
        encoding="utf-8",
        utc=False,  # Use local time
    )
    # Set the date suffix in YYYY-MM-DD format
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    worker_logger.addHandler(file_handler)

    # Log the initialization (debug level)
    worker_logger.debug(f"Worker started: {provider_alias}:{server_id}")

    return worker_logger


def configure_third_party_loggers(level: str = "WARNING") -> None:
    """
    Set the logging level for third-party libraries.

    Many libraries (httpx, aiogram, asyncio) produce a lot of DEBUG/INFO logs.
    This function raises their threshold to a higher level.

    Args:
        level: Logging level to apply to the third-party libraries

    Example:
        >>> configure_third_party_loggers("WARNING")
    """
    third_party_loggers = [
        "httpx",
        "httpcore",
        "aiogram",
        "aiogram.event",
        "aiogram.dispatcher",
        "asyncio",
    ]

    log_level = get_log_level(level)

    for logger_name in third_party_loggers:
        logging.getLogger(logger_name).setLevel(log_level)
