"""Tests for SQLAlchemy database models and SQLite optimizations."""

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    DuplicateGroup,
    DuplicateMember,
    File,
    FileEvent,
    ScanRun,
    utc_now,
)


@pytest.fixture
def db_session(tmp_path):
    """Create a temporary SQLite database session for testing with PRAGMAs."""
    db_file = tmp_path / "test_nas.db"
    test_engine = create_engine(
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

    Base.metadata.create_all(bind=test_engine)

    Session = sessionmaker(bind=test_engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        test_engine.dispose()


def test_sqlite_pragmas(db_session):
    """Verify foreign keys and WAL journal mode are enabled."""
    fk_result = db_session.execute(text("PRAGMA foreign_keys;")).scalar()
    assert fk_result == 1



def test_file_crud_and_status(db_session):
    """Test creating, reading, updating and soft-removing a File record."""
    now = utc_now()
    file_record = File(
        file_name="report001.pdf",
        file_path="/media/requisition/Report/report001.pdf",
        relative_path="report001.pdf",
        file_size=2048576,
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        first_seen_at=now,
        last_seen_at=now,
        is_present=True,
        is_duplicate=False,
        status="NEW",
    )
    db_session.add(file_record)
    db_session.commit()
    db_session.refresh(file_record)

    assert file_record.id is not None
    assert file_record.file_name == "report001.pdf"
    assert file_record.is_present is True

    # Update last seen
    later = utc_now()
    file_record.last_seen_at = later
    file_record.status = "EXISTING"
    db_session.commit()

    reloaded = db_session.scalar(select(File).where(File.id == file_record.id))
    assert reloaded.status == "EXISTING"
    assert reloaded.last_seen_at == later

    # Soft-remove
    removed_time = utc_now()
    reloaded.is_present = False
    reloaded.removed_at = removed_time
    reloaded.status = "REMOVED"
    db_session.commit()

    removed = db_session.scalar(select(File).where(File.id == file_record.id))
    assert removed.is_present is False
    assert removed.removed_at == removed_time
    assert removed.status == "REMOVED"


def test_file_unique_path_constraint(db_session):
    """Verify duplicate file_path raises IntegrityError."""
    now = utc_now()
    file1 = File(
        file_name="a.txt",
        file_path="/media/requisition/Report/a.txt",
        relative_path="a.txt",
        file_size=10,
        sha256="abc",
        first_seen_at=now,
        last_seen_at=now,
    )
    file2 = File(
        file_name="a.txt",
        file_path="/media/requisition/Report/a.txt",
        relative_path="a.txt",
        file_size=10,
        sha256="def",
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(file1)
    db_session.commit()

    db_session.add(file2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_file_events_history(db_session):
    """Verify FileEvent records maintain lifecycle events linked to File."""
    now = utc_now()
    file_record = File(
        file_name="report.pdf",
        file_path="/media/requisition/Report/report.pdf",
        relative_path="report.pdf",
        file_size=1024,
        sha256="hash123",
        first_seen_at=now,
        last_seen_at=now,
        status="NEW",
    )
    db_session.add(file_record)
    db_session.commit()

    event1 = FileEvent(
        file_id=file_record.id,
        event_type="ADDED",
        event_time=now,
        file_path=file_record.file_path,
        file_name=file_record.file_name,
        sha256=file_record.sha256,
        file_size=file_record.file_size,
        details="Initial detection",
    )
    event2 = FileEvent(
        file_id=file_record.id,
        event_type="SEEN",
        event_time=utc_now(),
        file_path=file_record.file_path,
        file_name=file_record.file_name,
        sha256=file_record.sha256,
        file_size=file_record.file_size,
        details="Subsequent scan confirmation",
    )
    db_session.add_all([event1, event2])
    db_session.commit()

    db_session.refresh(file_record)
    assert len(file_record.events) == 2
    event_types = [e.event_type for e in file_record.events]
    assert "ADDED" in event_types
    assert "SEEN" in event_types


def test_scan_run_lifecycle(db_session):
    """Verify ScanRun records scan metrics and completion status."""
    started = utc_now()
    scan = ScanRun(
        started_at=started,
        status="RUNNING",
    )
    db_session.add(scan)
    db_session.commit()
    assert scan.id is not None
    assert scan.status == "RUNNING"

    # Complete the scan
    scan.completed_at = utc_now()
    scan.status = "COMPLETED"
    scan.total_files = 150
    scan.new_files = 10
    scan.existing_files = 135
    scan.removed_files = 5
    scan.duplicate_files = 12
    db_session.commit()

    reloaded = db_session.scalar(select(ScanRun).where(ScanRun.id == scan.id))
    assert reloaded.status == "COMPLETED"
    assert reloaded.total_files == 150
    assert reloaded.new_files == 10
    assert reloaded.duplicate_files == 12


def test_duplicate_group_and_members(db_session):
    """Verify DuplicateGroup and DuplicateMember relationship with first/latest files."""
    t1 = utc_now()
    t2 = utc_now()
    shared_hash = "shared_hash_9876543210"

    file1 = File(
        file_name="orig.pdf",
        file_path="/media/requisition/Report/orig.pdf",
        relative_path="orig.pdf",
        file_size=5000,
        sha256=shared_hash,
        first_seen_at=t1,
        last_seen_at=t1,
        is_duplicate=True,
    )
    file2 = File(
        file_name="copy.pdf",
        file_path="/media/requisition/Report/archive/copy.pdf",
        relative_path="archive/copy.pdf",
        file_size=5000,
        sha256=shared_hash,
        first_seen_at=t2,
        last_seen_at=t2,
        is_duplicate=True,
    )
    db_session.add_all([file1, file2])
    db_session.commit()

    group = DuplicateGroup(
        sha256=shared_hash,
        first_file_id=file1.id,
        latest_file_id=file2.id,
        duplicate_count=2,
        first_seen_at=t1,
        latest_seen_at=t2,
    )
    db_session.add(group)
    db_session.commit()

    member1 = DuplicateMember(
        duplicate_group_id=group.id,
        file_id=file1.id,
        first_seen_at=t1,
        last_seen_at=t1,
    )
    member2 = DuplicateMember(
        duplicate_group_id=group.id,
        file_id=file2.id,
        first_seen_at=t2,
        last_seen_at=t2,
    )
    db_session.add_all([member1, member2])
    db_session.commit()

    db_session.refresh(group)
    assert group.duplicate_count == 2
    assert len(group.members) == 2
    assert group.first_file.file_name == "orig.pdf"
    assert group.latest_file.file_name == "copy.pdf"
