"""Tests for Phase 10: Scan Engine, ScanRun history, and Scenario E (Modified File)."""

from datetime import datetime, timezone
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, File, FileEvent, ScanRun
from app.services.file_service import FileService
from app.services.scan_service import ScanInProgressError, ScanService


@pytest.fixture
def db_session(tmp_path):
    """Fixture providing a fresh isolated SQLite DB session."""
    db_file = tmp_path / "test_engine.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA journal_mode=WAL;")
        finally:
            cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_full_scan_lifecycle_and_summary(tmp_path, db_session):
    """Verify scan_directory returns structured summary and creates a ScanRun record."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    f1 = nas_dir / "report1.pdf"
    f2 = nas_dir / "report2.pdf"
    f1.write_bytes(b"Report 1 content")
    f2.write_bytes(b"Report 2 content")

    summary = ScanService.scan_directory(db=db_session, directory=nas_dir)

    assert summary["status"] == "COMPLETED"
    assert summary["total_files"] == 2
    assert len(summary["new_files"]) == 2
    assert len(summary["existing_files"]) == 0
    assert len(summary["removed_files"]) == 0
    assert len(summary["errors"]) == 0

    # Verify ScanRun recorded in DB
    scan_run = ScanService.get_scan_run_by_id(db_session, summary["scan_id"])
    assert scan_run is not None
    assert scan_run.status == "COMPLETED"
    assert scan_run.total_files == 2
    assert scan_run.new_files == 2
    assert scan_run.completed_at is not None

    latest_run = ScanService.get_latest_scan_run(db_session)
    assert latest_run.id == scan_run.id


def test_scenario_e_file_content_modification(tmp_path, db_session):
    """Verify Scenario E: Content change creates a MODIFIED event and does NOT falsely create a duplicate."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    file_doc = nas_dir / "report.pdf"
    file_doc.write_bytes(b"Version 1 of confidential report")

    # Initial scan
    res_1 = ScanService.scan_directory(db=db_session, directory=nas_dir)
    assert len(res_1["new_files"]) == 1
    orig_hash = res_1["new_files"][0]["sha256"]

    # Modify file content on disk
    file_doc.write_bytes(b"Version 2 updated numbers and tables")

    # Second scan
    res_2 = ScanService.scan_directory(db=db_session, directory=nas_dir)

    assert len(res_2["modified_files"]) == 1
    mod_file = res_2["modified_files"][0]
    assert mod_file["file_name"] == "report.pdf"
    assert mod_file["sha256"] != orig_hash

    # Ensure not falsely called duplicate
    assert mod_file["is_duplicate"] is False
    assert len(res_2["duplicate_groups"]) == 0

    # Verify MODIFIED event in database
    events = FileService.get_file_events(db_session, file_id=mod_file["id"], event_type="MODIFIED")
    assert len(events) == 1
    assert orig_hash in events[0].details
    assert events[0].sha256 == mod_file["sha256"]


def test_scan_concurrency_lock(tmp_path, db_session):
    """Verify concurrent scan attempts are rejected when lock is active."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    from app.services.scan_service import _scan_lock

    # Acquire lock manually to simulate running scan
    _scan_lock.acquire()
    try:
        assert ScanService.is_scan_running() is True
        with pytest.raises(ScanInProgressError):
            ScanService.scan_directory(db=db_session, directory=nas_dir, enforce_lock=True)
    finally:
        _scan_lock.release()

    assert ScanService.is_scan_running() is False


def test_missing_directory_degraded_status(tmp_path, db_session):
    """Verify scanning non-existent NAS directory records DEGRADED status without crashing."""
    missing_dir = tmp_path / "offline_nas"

    summary = ScanService.scan_directory(db=db_session, directory=missing_dir)

    assert summary["status"] == "DEGRADED"
    assert summary["total_files"] == 0
    assert len(summary["errors"]) == 1

    latest_run = ScanService.get_latest_scan_run(db_session)
    assert latest_run.status == "DEGRADED"
    assert latest_run.error_count == 1
