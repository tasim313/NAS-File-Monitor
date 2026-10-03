"""Tests for Phase 7: Removed file detection, historical preservation, and restoration."""

from datetime import datetime, timezone
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, File, FileEvent, utc_now
from app.scanner.detector import ChangeDetector
from app.scanner.scanner import scan_directory_files
from app.services.file_service import FileService


@pytest.fixture
def db_session(tmp_path):
    """Fixture providing a fresh isolated SQLite DB session."""
    db_file = tmp_path / "test_removed.db"
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


def test_removed_file_detection_and_preservation(tmp_path, db_session):
    """Verify that deleting a file marks it REMOVED, stores removed_at, creates an event, and preserves DB row."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    f1 = nas_dir / "report001.pdf"
    f2 = nas_dir / "report002.pdf"
    f3 = nas_dir / "report003.pdf"

    f1.write_text("Report 1")
    f2.write_text("Report 2")
    f3.write_text("Report 3")

    detector = ChangeDetector(db_session)

    # Initial scan: all 3 added
    t1 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    res_1 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t1)
    db_session.commit()
    assert len(res_1["new_files"]) == 3
    assert FileService.count_removed_files(db_session) == 0

    # Delete report002.pdf from filesystem
    f2.unlink()
    assert not f2.exists()

    # Second scan: report002.pdf should be detected as REMOVED
    t2 = datetime(2026, 10, 2, 11, 0, 0, tzinfo=timezone.utc)
    res_2 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t2)
    db_session.commit()

    assert len(res_2["removed_files"]) == 1
    removed_f = res_2["removed_files"][0]
    assert removed_f.file_name == "report002.pdf"
    assert removed_f.is_present is False
    assert removed_f.status == "REMOVED"
    assert removed_f.removed_at == t2

    # Database records preserved: all 3 rows remain in SQLite
    all_files = list(db_session.scalars(select(File)).all())
    assert len(all_files) == 3

    # Verify query for removed files
    removed_list = FileService.get_removed_files(db_session)
    assert len(removed_list) == 1
    assert removed_list[0].file_name == "report002.pdf"
    assert FileService.count_removed_files(db_session) == 1

    # Verify REMOVED event was recorded
    events = FileService.get_file_events(db_session, file_id=removed_f.id)
    event_types = [e.event_type for e in events]
    assert "ADDED" in event_types
    assert "REMOVED" in event_types


def test_restored_file_scenario(tmp_path, db_session):
    """Verify Scenario D: removed file later returned is marked RESTORED with historical continuity."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    f = nas_dir / "report.pdf"
    f.write_text("Original content")

    detector = ChangeDetector(db_session)

    # Scan 1: Added
    t1 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
    detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t1)
    db_session.commit()

    # Delete file
    f.unlink()

    # Scan 2: Removed
    t2 = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t2)
    db_session.commit()
    assert FileService.count_removed_files(db_session) == 1

    # Return file to filesystem
    f.write_text("Original content returned")

    # Scan 3: Restored
    t3 = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc)
    res_3 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t3)
    db_session.commit()

    assert len(res_3["restored_files"]) == 1
    restored_f = res_3["restored_files"][0]
    assert restored_f.file_name == "report.pdf"
    assert restored_f.is_present is True
    assert restored_f.removed_at is None
    assert restored_f.status == "EXISTING"
    assert restored_f.first_seen_at == t1  # Original first detection preserved!
    assert restored_f.last_seen_at == t3

    # Database still has only 1 record (no duplicate rows)
    assert len(list(db_session.scalars(select(File)).all())) == 1

    # Verify event audit history: ADDED -> REMOVED -> RESTORED
    events = FileService.get_file_events(db_session, file_id=restored_f.id)
    event_types = [e.event_type for e in events]
    assert event_types == ["RESTORED", "REMOVED", "ADDED"]
