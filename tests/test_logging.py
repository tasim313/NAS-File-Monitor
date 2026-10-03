"""Tests for application logging configuration."""

from app.logging_config import get_logger, setup_logging


def test_setup_logging(tmp_path):
    """Verify setup_logging creates expected log files and writes messages."""
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, log_level="DEBUG")

    logger = get_logger("app.test")
    scanner_logger = get_logger("app.scanner")

    logger.info("Test application log message")
    scanner_logger.info("Test scanner log message")
    logger.error("Test error log message")

    app_log = log_dir / "app.log"
    scanner_log = log_dir / "scanner.log"
    error_log = log_dir / "error.log"

    assert app_log.exists()
    assert scanner_log.exists()
    assert error_log.exists()

    app_content = app_log.read_text(encoding="utf-8")
    assert "Test application log message" in app_content
    assert "Test error log message" in app_content

    scanner_content = scanner_log.read_text(encoding="utf-8")
    assert "Test scanner log message" in scanner_content

    error_content = error_log.read_text(encoding="utf-8")
    assert "Test error log message" in error_content
