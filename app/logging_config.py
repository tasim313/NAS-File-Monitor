"""Application logging configuration.

Sets up console and rotating file logs for general app, scanner, and errors.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.config import get_settings


def setup_logging(
    log_dir: Optional[Path] = None,
    log_level: Optional[str] = None,
) -> None:
    """Configure root, scanner, and error loggers with console and rotating file handlers."""
    settings = get_settings()
    target_log_dir = log_dir or settings.LOG_DIR
    target_log_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, (log_level or settings.LOG_LEVEL).upper(), logging.INFO)

    # Standard formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Detailed formatter for error log
    error_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | [%(filename)s:%(lineno)d] | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers to prevent duplicate logs
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 1. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. General App Log File (10MB max, 5 backups)
    app_log_path = target_log_dir / "app.log"
    app_file_handler = RotatingFileHandler(
        filename=str(app_log_path),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_file_handler.setLevel(numeric_level)
    app_file_handler.setFormatter(formatter)
    root_logger.addHandler(app_file_handler)

    # 3. Dedicated Error Log File (10MB max, 5 backups)
    error_log_path = target_log_dir / "error.log"
    error_file_handler = RotatingFileHandler(
        filename=str(error_log_path),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(error_formatter)
    root_logger.addHandler(error_file_handler)

    # 4. Scanner Logger and dedicated log file
    scanner_logger = logging.getLogger("app.scanner")
    scanner_log_path = target_log_dir / "scanner.log"
    scanner_file_handler = RotatingFileHandler(
        filename=str(scanner_log_path),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    scanner_file_handler.setLevel(numeric_level)
    scanner_file_handler.setFormatter(formatter)
    scanner_logger.addHandler(scanner_file_handler)

    # Silence overly verbose third-party loggers if needed
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Helper to get a logger by name."""
    return logging.getLogger(name)
