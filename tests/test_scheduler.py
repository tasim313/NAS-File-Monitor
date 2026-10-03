"""Tests for Phase 11: Background periodic scan scheduler and manual scan triggers."""

from unittest.mock import patch
import pytest

from app.scanner.scheduler import ScanScheduler, get_scan_scheduler
from app.services.scan_service import ScanInProgressError, _scan_lock


def test_scheduler_lifecycle():
    """Verify scheduler starts, reports active status and next run time, and shuts down."""
    scheduler = ScanScheduler(interval_seconds=30)
    assert scheduler.is_running is False

    scheduler.start()
    assert scheduler.is_running is True

    status = scheduler.get_status()
    assert status["is_running"] is True
    assert status["interval_seconds"] == 30
    assert status["next_run_time"] is not None

    scheduler.stop(wait=False)
    assert scheduler.is_running is False


def test_trigger_manual_scan_execution(tmp_path):
    """Verify trigger_manual_scan performs an on-demand scan."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()
    (nas_dir / "doc.txt").write_text("sample content")

    scheduler = ScanScheduler()
    summary = scheduler.trigger_manual_scan(directory=nas_dir)
    assert summary["status"] == "COMPLETED"
    assert summary["total_files"] == 1
    assert len(summary["new_files"]) == 1



def test_trigger_manual_scan_concurrency_rejection(tmp_path):
    """Verify manual scan request is rejected with ScanInProgressError when another scan is active."""
    scheduler = ScanScheduler()

    _scan_lock.acquire()
    try:
        with pytest.raises(ScanInProgressError):
            scheduler.trigger_manual_scan()
    finally:
        _scan_lock.release()


def test_execute_scheduled_scan_skips_when_busy(caplog):
    """Verify scheduled periodic job logs warning and gracefully skips when another scan is running."""
    scheduler = ScanScheduler()

    _scan_lock.acquire()
    try:
        scheduler._execute_scheduled_scan()
        assert "another scan is already in progress" in caplog.text
    finally:
        _scan_lock.release()
