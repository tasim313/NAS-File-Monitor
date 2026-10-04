"""Tests for Phase 8 & Phase 9: SHA-256 duplicate detection and first vs latest occurrence tracking."""

from datetime import datetime, timezone
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, DuplicateGroup, DuplicateMember, File, FileEvent, utc_now
from app.scanner.detector import ChangeDetector
from app.scanner.scanner import scan_directory_files
from app.services.duplicate_service import DuplicateService
from app.services.file_service import FileService


@pytest.fixture
def db_session(tmp_path):
    """Fixture providing a fresh isolated SQLite DB session."""
    db_file = tmp_path / "test_duplicates.db"
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


def test_scenario_a_same_filename_different_content_not_duplicate(tmp_path, db_session):
    """Verify Scenario A: Same filename in different directories with different SHA-256 are NOT duplicates."""
    nas_dir = tmp_path / "nas_test"
    dir_a = nas_dir / "DeptA"
    dir_b = nas_dir / "DeptB"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)

    file_a = dir_a / "report.pdf"
    file_b = dir_b / "report.pdf"

    file_a.write_text("Unique content A for Department A")
    file_b.write_text("Completely different content B for Department B")

    detector = ChangeDetector(db_session)
    res = detector.process_scan(scan_directory_files(nas_dir).files)
    db_session.commit()

    assert len(res["new_files"]) == 2
    assert len(res["duplicate_groups"]) == 0
    assert DuplicateService.count_duplicate_groups(db_session) == 0
    assert DuplicateService.count_duplicate_files(db_session) == 0

    for f in res["new_files"]:
        assert f.is_duplicate is False


def test_scenario_b_different_filename_same_content_is_duplicate(tmp_path, db_session):
    """Verify Scenario B: Different filenames with identical SHA-256 are recognized as duplicates."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    shared_content = b"Exact identical duplicate report payload SHA-256 test"

    # Scan 1: Original file appears
    file_orig = nas_dir / "report.pdf"
    file_orig.write_bytes(shared_content)

    detector = ChangeDetector(db_session)
    t1 = datetime(2026, 10, 1, 9, 15, 20, tzinfo=timezone.utc)
    res_1 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t1)
    db_session.commit()

    assert len(res_1["new_files"]) == 1
    assert res_1["new_files"][0].is_duplicate is False
    assert DuplicateService.count_duplicate_groups(db_session) == 0

    # Scan 2: Duplicate copy appears later in an archive directory
    archive_dir = nas_dir / "archive"
    archive_dir.mkdir()
    file_copy = archive_dir / "report-copy.pdf"
    file_copy.write_bytes(shared_content)

    t2 = datetime(2026, 10, 3, 13, 40, 12, tzinfo=timezone.utc)
    res_2 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t2)
    db_session.commit()

    assert len(res_2["new_files"]) == 1
    assert len(res_2["duplicate_groups"]) == 1
    assert DuplicateService.count_duplicate_groups(db_session) == 1
    assert DuplicateService.count_duplicate_files(db_session) == 2

    # Verify duplicate group attributes
    group = res_2["duplicate_groups"][0]
    assert group.duplicate_count == 2
    assert group.first_file.file_name == "report.pdf"
    assert group.first_file.file_path == str(file_orig.resolve())
    assert group.first_seen_at == t1

    assert group.latest_file.file_name == "report-copy.pdf"
    assert group.latest_file.file_path == str(file_copy.resolve())
    assert group.latest_seen_at == t2

    # Verify members
    members = DuplicateService.get_duplicate_group_files(db_session, group.id)
    assert len(members) == 2
    member_names = [m.file_name for m in members]
    assert member_names == ["report.pdf", "report-copy.pdf"]

    # Verify DUPLICATE_DETECTED event created
    events = FileService.get_file_events(db_session, event_type="DUPLICATE_DETECTED")
    assert len(events) >= 1


def test_scenario_c_unchanged_file_remains_existing_not_duplicate_of_itself(tmp_path, db_session):
    """Verify Scenario C: Unchanged existing file does NOT create a duplicate group with itself."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    f = nas_dir / "single.pdf"
    f.write_text("Single file content")

    detector = ChangeDetector(db_session)
    detector.process_scan(scan_directory_files(nas_dir).files)
    db_session.commit()

    # Subsequent scan
    res_2 = detector.process_scan(scan_directory_files(nas_dir).files)
    db_session.commit()

    assert len(res_2["existing_files"]) == 1
    assert len(res_2["duplicate_groups"]) == 0
    assert DuplicateService.count_duplicate_groups(db_session) == 0


def test_first_vs_latest_occurrence_with_three_copies(tmp_path, db_session):
    """Verify first occurrence is preserved and latest occurrence updates when a 3rd copy appears."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()
    data = b"Three copies test content"

    detector = ChangeDetector(db_session)

    # Copy 1 at t1
    f1 = nas_dir / "f1.pdf"
    f1.write_bytes(data)
    t1 = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
    detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t1)
    db_session.commit()

    # Copy 2 at t2
    f2 = nas_dir / "f2.pdf"
    f2.write_bytes(data)
    t2 = datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t2)
    db_session.commit()

    # Copy 3 at t3
    f3 = nas_dir / "f3.pdf"
    f3.write_bytes(data)
    t3 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)
    res_3 = detector.process_scan(scan_directory_files(nas_dir).files, scan_time=t3)
    db_session.commit()

    group = res_3["duplicate_groups"][0]
    assert group.duplicate_count == 3
    # First must still be f1 from t1!
    assert group.first_file.file_name == "f1.pdf"
    assert group.first_seen_at == t1
    # Latest must be f3 from t3!
    assert group.latest_file.file_name == "f3.pdf"
    assert group.latest_seen_at == t3


def test_duplicate_file_removal_reduces_group_count(tmp_path, db_session):
    """Verify that removing a duplicate copy updates duplicate count and resets is_duplicate if only 1 remains."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()
    data = b"Duplicate removal content test"

    f1 = nas_dir / "copy1.txt"
    f2 = nas_dir / "copy2.txt"
    f1.write_bytes(data)
    f2.write_bytes(data)

    detector = ChangeDetector(db_session)
    detector.process_scan(scan_directory_files(nas_dir).files)
    db_session.commit()

    assert DuplicateService.count_duplicate_groups(db_session) == 1
    assert DuplicateService.count_duplicate_files(db_session) == 2

    # Remove copy2.txt
    f2.unlink()

    detector.process_scan(scan_directory_files(nas_dir).files)
    db_session.commit()

    # Since only 1 copy remains present, active duplicate count is 0
    assert DuplicateService.count_duplicate_groups(db_session) == 0
    assert DuplicateService.count_duplicate_files(db_session) == 0

    # Remaining copy1 is no longer marked as duplicate
    reloaded_f1 = db_session.scalar(select(File).where(File.file_name == "copy1.txt"))
    assert reloaded_f1.is_duplicate is False


def test_extract_duplicate_key():
    """Verify extraction of duplicate key prefix before first underscore."""
    from app.services.duplicate_service import extract_duplicate_key

    assert extract_duplicate_key("2609-32907_126556.pdf") == "2609-32907"
    assert extract_duplicate_key("2609-32907_126552.pdf") == "2609-32907"
    assert extract_duplicate_key("2610-00287_126667.pdf") == "2610-00287"
    assert extract_duplicate_key("2610-00287_126669.pdf") == "2610-00287"
    assert extract_duplicate_key("2610-00286_126662.pdf") == "2610-00286"
    assert extract_duplicate_key("2609-35838_20261003_172852.pdf") == "2609-35838"
    assert extract_duplicate_key("report.pdf") is None
    assert extract_duplicate_key("HPL2609-00280.pdf") is None
    assert extract_duplicate_key("_invalid.pdf") is None


def test_requisition_number_duplicate_detection(tmp_path, db_session):
    """Verify that files sharing requisition prefix (e.g. 2609-32907) are detected as duplicates even with different content."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir()

    # Requisition 2609-32907 has 2 copies with different contents
    f1 = nas_dir / "2609-32907_126552.pdf"
    f1.write_bytes(b"Requisition 2609-32907 print payload 1")
    f2 = nas_dir / "2609-32907_126556.pdf"
    f2.write_bytes(b"Requisition 2609-32907 print payload 2 with slightly different timestamp")

    # Requisition 2610-00287 has 2 copies with different contents
    f3 = nas_dir / "2610-00287_126667.pdf"
    f3.write_bytes(b"Requisition 2610-00287 initial print")
    f4 = nas_dir / "2610-00287_126669.pdf"
    f4.write_bytes(b"Requisition 2610-00287 re-print copy")

    # Requisition 2610-00286 has only 1 unique copy
    f5 = nas_dir / "2610-00286_126662.pdf"
    f5.write_bytes(b"Unique Requisition 2610-00286 single print")

    # Unique file without underscore
    f6 = nas_dir / "HPL2609-00280.pdf"
    f6.write_bytes(b"Standard file without underscore")

    detector = ChangeDetector(db_session)
    res = detector.process_scan(scan_directory_files(nas_dir).files)
    db_session.commit()

    assert len(res["new_files"]) == 6
    assert len(res["duplicate_groups"]) == 2
    assert DuplicateService.count_duplicate_groups(db_session) == 2
    assert DuplicateService.count_duplicate_files(db_session) == 4

    # Group 1: 2609-32907
    grp_32907 = DuplicateService.get_duplicate_group_by_hash(db_session, "2609-32907")
    assert grp_32907 is not None
    assert grp_32907.duplicate_count == 2
    assert grp_32907.first_file.file_name == "2609-32907_126552.pdf"
    assert grp_32907.latest_file.file_name == "2609-32907_126556.pdf"

    # Group 2: 2610-00287
    grp_00287 = DuplicateService.get_duplicate_group_by_hash(db_session, "2610-00287")
    assert grp_00287 is not None
    assert grp_00287.duplicate_count == 2
    assert grp_00287.first_file.file_name == "2610-00287_126667.pdf"
    assert grp_00287.latest_file.file_name == "2610-00287_126669.pdf"

    # Verify unique files
    file_unique = db_session.scalar(select(File).where(File.file_name == "2610-00286_126662.pdf"))
    assert file_unique is not None
    assert file_unique.is_duplicate is False

    file_hpl = db_session.scalar(select(File).where(File.file_name == "HPL2609-00280.pdf"))
    assert file_hpl is not None
    assert file_hpl.is_duplicate is False

    # Verify members
    members_32907 = DuplicateService.get_duplicate_group_files(db_session, grp_32907.id)
    assert len(members_32907) == 2
    assert {m.file_name for m in members_32907} == {"2609-32907_126552.pdf", "2609-32907_126556.pdf"}
    for m in members_32907:
        assert m.is_duplicate is True
