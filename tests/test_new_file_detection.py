"""Tests for Phase 5: New File Detection and ADDED event recording."""

from datetime import datetime, timezone
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, File, FileEvent, utc_now
from app.scanner.detector import ChangeDetector
from app.scanner.hasher import calculate_sha256
from app.scanner.models import ScannedFile
from app.scanner.scanner import scan_directory_files
from app.services.file_service import FileService


@pytest.fixture
def db_session(tmp_path):
    """Fixture providing a fresh isolated SQLite DB session."""
    db_file = tmp_path / "test_detection.db"
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


def test_detect_new_files_in_empty_db(tmp_path, db_session):
    """Verify scanning an empty database detects all files as NEW and logs ADDED events."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    file1 = nas_dir / "report001.pdf"
    file1.write_bytes(b"Report 1 content data")

    file2 = nas_dir / "sub" / "report002.pdf"
    file2.parent.mkdir()
    file2.write_bytes(b"Report 2 different content data")

    discovery = scan_directory_files(nas_dir)
    assert discovery.total_count == 2

    detector = ChangeDetector(db_session)
    scan_time = utc_now()
    new_files, errors = detector.detect_new_files(discovery.files, scan_time=scan_time)
    db_session.commit()

    assert len(errors) == 0
    assert len(new_files) == 2

    # Check File records in database
    db_files = list(db_session.scalars(select(File)).all())
    assert len(db_files) == 2

    for f in db_files:
        assert f.status == "NEW"
        assert f.is_present is True
        assert f.is_duplicate is False
        assert f.first_seen_at == scan_time
        assert f.last_seen_at == scan_time
        assert len(f.sha256) == 64

        # Verify ADDED event was created and linked
        assert len(f.events) == 1
        event = f.events[0]
        assert event.event_type == "ADDED"
        assert event.file_id == f.id
        assert event.sha256 == f.sha256
        assert event.event_time == scan_time

    # Verify query for newly added files
    new_list = FileService.get_newly_added_files(db_session)
    assert len(new_list) == 2
    assert FileService.count_newly_added_files(db_session) == 2


def test_repeated_scan_does_not_duplicate_new_files(tmp_path, db_session):
    """Verify that a second scan does not re-register already recorded files as NEW."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    file1 = nas_dir / "report001.pdf"
    file1.write_bytes(b"Report 1 content")

    discovery = scan_directory_files(nas_dir)
    detector = ChangeDetector(db_session)

    # First scan: detected as new
    new_files_1, errors_1 = detector.detect_new_files(discovery.files)
    db_session.commit()
    assert len(new_files_1) == 1
    assert len(errors_1) == 0

    # Second scan with same discovery:
    new_files_2, errors_2 = detector.detect_new_files(discovery.files)
    db_session.commit()
    assert len(new_files_2) == 0
    assert len(errors_2) == 0

    # Total files in DB remains 1
    total_db_files = list(db_session.scalars(select(File)).all())
    assert len(total_db_files) == 1


def test_detect_new_file_hashing_error_handling(tmp_path, db_session):
    """Verify that a read/hashing error on one file is handled safely without failing other files."""
    valid_file = ScannedFile(
        file_name="valid.pdf",
        file_path=str(tmp_path / "valid.pdf"),
        relative_path="valid.pdf",
        file_size=100,
        mtime=utc_now(),
    )
    Path(valid_file.file_path).write_bytes(b"valid content")

    missing_file = ScannedFile(
        file_name="vanished.pdf",
        file_path=str(tmp_path / "vanished.pdf"),
        relative_path="vanished.pdf",
        file_size=200,
        mtime=utc_now(),
    )
    # vanished.pdf does not exist on disk (simulating file disappeared during copy)

    detector = ChangeDetector(db_session)
    new_files, errors = detector.detect_new_files([valid_file, missing_file])
    db_session.commit()

    assert len(new_files) == 1
    assert new_files[0].file_name == "valid.pdf"
    assert len(errors) == 1
    assert "vanished.pdf" in errors[0]["path"]
