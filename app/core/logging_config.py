"""
Centralized logging configuration with file rotation support.
"""
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

# Track which loggers have been configured to avoid duplicate setup
_configured_loggers = set()


def setup_logging(
    log_name: str = "radio_capture",
    level: int = logging.INFO,
    log_file_name: Optional[str] = None
) -> logging.Logger:
    """
    Configure logging with optional file output and rotation.
    
    Args:
        log_name: Name for the logger
        level: Logging level (default: INFO)
        log_file_name: Optional custom filename for the log file (without .log extension)
                      If not provided, uses log_name
    
    Returns:
        Configured logger instance
    
    Environment Variables:
        ENABLE_RADIO_LOGS: Set to 'true' or '1' to enable disk logging
        LOG_DIR: Directory for log files (default: /data/logs)
    """
    logger = logging.getLogger(log_name)

    # Prevent duplicate handlers if called multiple times
    if log_name in _configured_loggers:
        return logger

    _configured_loggers.add(log_name)
    logger.setLevel(level)
    logger.propagate = False  # Don't propagate to parent loggers

    _attach_handlers(logger, log_file_name if log_file_name else log_name)

    return logger


def _attach_handlers(logger: logging.Logger, file_base: str) -> None:
    """
    Attach a stdout handler (always) and a rotating file handler (if enabled)
    to the given logger.

    Args:
        logger: Logger to configure
        file_base: Base filename (without .log) used when disk logging is on
    """
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Always add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Add file handler if enabled
    enable_logs = os.getenv('ENABLE_RADIO_LOGS', '').lower() in ('true', '1', 'yes')

    if enable_logs:
        log_dir = Path(os.getenv('LOG_DIR', '/data/logs'))

        try:
            # Create log directory if it doesn't exist
            log_dir.mkdir(parents=True, exist_ok=True)

            # Sanitize filename (replace special characters with underscores)
            file_base = "".join(c if c.isalnum() or c in ('-', '_', '.') else '_' for c in file_base)

            # Set up rotating file handler (rotates every 3 days)
            log_file = log_dir / f"{file_base}.log"
            file_handler = TimedRotatingFileHandler(
                filename=str(log_file),
                when='D',  # Rotate daily
                interval=3,  # Every 3 days
                backupCount=10,  # Keep 10 backup files (30 days worth)
                encoding='utf-8',
                utc=True
            )
            file_handler.setFormatter(formatter)
            file_handler.suffix = "%Y-%m-%d"  # Add date suffix to rotated files
            logger.addHandler(file_handler)

            # Only log the setup message once per logger to avoid spam
            logger.info(f"File logging enabled: {log_file}")

        except Exception as e:
            logger.error(f"Failed to set up file logging: {e}")
            logger.warning("Continuing with console logging only")
    else:
        logger.info(
            "Disk logging is disabled (ENABLE_RADIO_LOGS is not set to true/1/yes); "
            "console output only"
        )


def configure_app_logging(log_file_name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Configure the 'app' package logger.

    Modules under app/ use ``logging.getLogger(__name__)`` ("app.services.watcher",
    "app.services.stream_manager", "app.services.asr", ...). Those loggers have no
    handlers of their own, and the root logger is never configured, so without this
    call their records fall through to logging.lastResort, which drops everything
    below WARNING and never reaches the log files. That means the whole capture /
    classification / ASR pipeline used to run effectively unlogged.

    Call this once per process (API server, watcher) with the log file the process
    already writes to, so app.* records land in the same place.

    Args:
        log_file_name: Base filename (without .log) to write app.* records to
        level: Logging level for the app package

    Returns:
        The configured 'app' logger
    """
    app_logger = logging.getLogger("app")

    if "app" in _configured_loggers:
        return app_logger

    _configured_loggers.add("app")
    app_logger.setLevel(level)
    app_logger.propagate = False

    _attach_handlers(app_logger, log_file_name)
    app_logger.info("app.* package logging configured (level=%s)", logging.getLevelName(level))

    return app_logger


def log_unhandled_exceptions(logger: logging.Logger) -> None:
    """
    Route uncaught exceptions through the given logger instead of a bare
    stderr traceback, so a crashing process leaves a trace in the log files.
    """
    def _handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Uncaught exception — process is terminating",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = _handler


def get_stream_logger(stream_name: str, stream_id: int) -> logging.Logger:
    """
    Get a logger for a specific stream's ffmpeg process.
    Creates a separate log file for each stream.
    
    Args:
        stream_name: Name of the stream
        stream_id: ID of the stream
    
    Returns:
        Logger instance for the stream
    """
    logger_name = f"radio_capture.stream.{stream_id}.{stream_name}"
    log_file_name = f"stream_{stream_id}_{stream_name}"
    return setup_logging(logger_name, log_file_name=log_file_name)


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance with the configured settings.
    
    Args:
        name: Logger name (will be appended to 'radio_capture')
    
    Returns:
        Logger instance
    """
    if name:
        full_name = f"radio_capture.{name}"
    else:
        full_name = "radio_capture"
    
    return logging.getLogger(full_name)
