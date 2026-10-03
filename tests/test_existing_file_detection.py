"""Tests for Phase 6: Existing File Detection and Previously Added Files list maintenance."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
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
    db_file = tmp_path / "test_existing.db"
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


def test_existing_file_transition_and_timestamps(tmp_path, db_session):
    """Verify that an existing file transitions from NEW to EXISTING and updates last_seen_at."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    file_path = nas_dir / "report001.pdf"
    file_path.write_bytes(b"Initial report document content")

    detector = ChangeDetector(db_session)

    # --- Scan 1: Initial Discovery ---
    t1 = datetime(2026, 10, 2, 10, 15, 20, tzinfo=timezone.utc)
    discovery_1 = scan_directory_files(nas_dir)

    result_1 = detector.process_scan(discovery_1.files, scan_time=t1)
    db_session.commit()

    assert len(result_1["new_files"]) == 1
    assert len(result_1["existing_files"]) == 0
    f1 = result_1["new_files"][0]
    assert f1.status == "NEW"
    assert f1.first_seen_at == t1
    assert f1.last_seen_at == t1

    # In List 1 (Newly Added), not in List 2 (Previously Added)
    assert FileService.count_newly_added_files(db_session) == 1
    assert FileService.count_previously_added_files(db_session) == 0

    # --- Scan 2: Subsequent Scan (24 hours later) ---
    t2 = datetime(2026, 10, 3, 14, 10, 25, tzinfo=timezone.utc)
    discovery_2 = scan_directory_files(nas_dir)

    result_2 = detector.process_scan(discovery_2.files, scan_time=t2)
    db_session.commit()

    assert len(result_2["new_files"]) == 0
    assert len(result_2["existing_files"]) == 1

    # Verify database record: NO duplicate row created
    all_files = list(db_session.scalars(select(File)).all())
    assert len(all_files) == 1

    existing_file = all_files[0]
    assert existing_file.status == "EXISTING"
    assert existing_file.first_seen_at == t1  # Historical first occurrence preserved
    assert existing_file.last_seen_at == t2   # Last seen updated to latest scan
    assert existing_file.is_present is True

    # No duplicate ADDED event was created
    events = list(db_session.scalars(select(FileEvent)).all())
    assert len(events) == 1
    assert events[0].event_type == "ADDED"

    # Now in List 2 (Previously Added), removed from List 1 (Newly Added)
    assert FileService.count_newly_added_files(db_session) == 0
    assert FileService.count_previously_added_files(db_session) == 1

    prev_list = FileService.get_previously_added_files(db_session)
    assert len(prev_list) == 1
    assert prev_list[0].file_name == "report001.pdf"


def test_mixed_scan_separates_new_and_existing_files(tmp_path, db_session):
    """Verify that a scan with both old and new files correctly classifies each into separate lists."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    old_file_1 = nas_dir / "doc1.txt"
    old_file_1.write_text("Document 1")

    old_file_2 = nas_dir / "doc2.txt"
    old_file_2.write_text("Document 2")

    detector = ChangeDetector(db_session)

    # Initial scan: register doc1 and doc2
    t1 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
    res_1 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t1)
    db_session.commit()
    assert len(res_1["new_files"]) == 2

    # Second scan: transition them to EXISTING
    t2 = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t2)
    db_session.commit()
    assert FileService.count_previously_added_files(db_session) == 2

    # Add a brand new third file
    new_file = nas_dir / "doc3_brand_new.txt"
    new_file.write_text("Brand new document")

    # Third scan: should detect 1 new and 2 existing
    t3 = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc)
    res_3 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t3)
    db_session.commit()

    assert len(res_3["new_files"]) == 1
    assert res_3["new_files"][0].file_name == "doc3_brand_new.txt"
    assert res_3["new_files"][0].status == "NEW"

    assert len(res_3["existing_files"]) == 2
    existing_names = {f.file_name for f in res_3["existing_files"]}
    assert existing_names == {"doc1.txt", "doc2.txt"}

    # Total in DB is exactly 3
    assert len(list(db_session.scalars(select(File)).all())) == 3

    # List separation check:
    assert FileService.count_newly_added_files(db_session) == 1
    assert FileService.count_previously_added_files(db_session) == 2


def test_existing_files_do_not_recalculate_hash(tmp_path, db_session):
    """Verify performance optimization: existing unchanged files do not recompute SHA-256."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    file_a = nas_dir / "existing_doc.pdf"
    file_a.write_bytes(b"Static document payload")

    detector = ChangeDetector(db_session)

    # Initial scan
    detector.process_scan(scan_directory_files(nas_dir).files)
    db_session.commit()

    # Second scan on existing file: verify calculate_sha256_safe is not called
    with patch("app.scanner.detector.calculate_sha256_safe") as mock_hasher:
        detector.process_scan(scan_directory_files(nas_dir).files)
        db_session.commit()
        mock_hasher.assert_not_called()
