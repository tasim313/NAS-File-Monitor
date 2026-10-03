"""Tests for Phase 12: FastAPI REST API endpoints."""

from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.database.database import get_db
from app.database.models import Base
from app.main import app
from app.services.duplicate_service import DuplicateService
from app.services.file_service import FileService
from app.services.scan_service import _scan_lock, ScanService


@pytest.fixture
def db_session(tmp_path):
    """Fixture providing a fresh isolated SQLite DB session."""
    db_file = tmp_path / "test_api.db"
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
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    """Fixture providing TestClient with overridden get_db dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def populated_db(db_session, tmp_path):
    """Populate test database with new, existing, removed, and duplicate files."""
    nas_dir = tmp_path / "nas_test"
    nas_dir.mkdir(parents=True, exist_ok=True)

    f1 = nas_dir / "report1.pdf"
    f2 = nas_dir / "report2_dup.pdf"
    f3 = nas_dir / "unique.txt"

    f1.write_bytes(b"SAME_CONTENT_FOR_DUPLICATES")
    f2.write_bytes(b"SAME_CONTENT_FOR_DUPLICATES")
    f3.write_bytes(b"UNIQUE_CONTENT_DATA")

    # Perform initial scan
    summary = ScanService.scan_directory(db_session, directory=nas_dir, enforce_lock=False)
    assert summary["status"] == "COMPLETED"

    # Now let's remove f3 and do a second scan to mark it REMOVED and others EXISTING
    f3.unlink()
    summary2 = ScanService.scan_directory(db_session, directory=nas_dir, enforce_lock=False)
    assert summary2["status"] == "COMPLETED"

    # Add a brand new file
    f4 = nas_dir / "brand_new.csv"
    f4.write_text("col1,col2\n1,2")
    summary3 = ScanService.scan_directory(db_session, directory=nas_dir, enforce_lock=False)
    assert summary3["status"] == "COMPLETED"

    return {"dir": nas_dir, "db": db_session}


def test_get_dashboard_stats(client, populated_db):
    """Verify GET /api/dashboard returns aggregate counts, status, and recent scans/events."""
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()

    assert "system" in data
    assert "stats" in data
    assert "latest_scan" in data
    assert "recent_events" in data

    stats = data["stats"]
    assert stats["total_files"] >= 3  # report1, report2_dup, brand_new are present
    assert stats["removed_files"] >= 1  # unique.txt is removed
    assert stats["duplicate_groups"] >= 1  # report1 and report2_dup form a group
    assert stats["duplicate_files"] >= 2

    assert data["latest_scan"] is not None
    assert len(data["recent_events"]) > 0


def test_list_files_and_filtering(client, populated_db):
    """Verify GET /api/files supports pagination and filtering by presence/status/query."""
    # List all files
    res = client.get("/api/files")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 4
    assert len(data["items"]) >= 4

    # Filter by query
    res_query = client.get("/api/files?query=brand_new")
    assert res_query.status_code == 200
    data_q = res_query.json()
    assert data_q["total"] == 1
    assert data_q["items"][0]["file_name"] == "brand_new.csv"

    # Filter by is_present=false
    res_removed = client.get("/api/files?is_present=false")
    assert res_removed.status_code == 200
    data_rem = res_removed.json()
    assert data_rem["total"] >= 1
    assert any(f["file_name"] == "unique.txt" for f in data_rem["items"])

    # Filter by is_duplicate=true
    res_dup = client.get("/api/files?is_duplicate=true")
    assert res_dup.status_code == 200
    data_dup = res_dup.json()
    assert data_dup["total"] == 2


def test_list_new_files(client, populated_db):
    """Verify GET /api/files/new returns only newly detected files (status='NEW')."""
    res = client.get("/api/files/new")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert all(f["status"] == "NEW" for f in data["items"])
    assert any(f["file_name"] == "brand_new.csv" for f in data["items"])


def test_list_previous_files(client, populated_db):
    """Verify GET /api/files/previous returns existing files (status='EXISTING')."""
    res = client.get("/api/files/previous")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 2
    assert all(f["status"] == "EXISTING" for f in data["items"])


def test_list_removed_files(client, populated_db):
    """Verify GET /api/files/removed returns soft-deleted files (is_present=False)."""
    res = client.get("/api/files/removed")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert all(f["is_present"] is False for f in data["items"])
    assert any(f["file_name"] == "unique.txt" for f in data["items"])


def test_list_duplicates(client, populated_db):
    """Verify GET /api/files/duplicates returns duplicate groups with first/latest files and members."""
    res = client.get("/api/files/duplicates")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert len(data["groups"]) >= 1

    group = data["groups"][0]
    assert group["duplicate_count"] == 2
    assert "first_file" in group
    assert "latest_file" in group
    assert len(group["members"]) == 2


def test_get_file_details_and_not_found(client, populated_db):
    """Verify GET /api/files/{id} returns metadata and timeline, and 404 for invalid ID."""
    # Find a valid file ID
    res_list = client.get("/api/files?query=report1.pdf")
    file_id = res_list.json()["items"][0]["id"]

    res = client.get(f"/api/files/{file_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == file_id
    assert data["file_name"] == "report1.pdf"
    assert "events" in data
    assert len(data["events"]) >= 1
    assert "duplicate_groups" in data
    assert len(data["duplicate_groups"]) >= 1

    # Test 404 for non-existent file
    res_404 = client.get("/api/files/999999")
    assert res_404.status_code == 404


def test_list_file_events(client, populated_db):
    """Verify GET /api/events returns chronological audit events and supports filtering."""
    res = client.get("/api/events")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] > 0
    assert len(data["items"]) > 0

    # Filter by event_type=REMOVED
    res_rem = client.get("/api/events?event_type=REMOVED")
    assert res_rem.status_code == 200
    assert all(e["event_type"] == "REMOVED" for e in res_rem.json()["items"])


def test_list_and_get_scan_runs(client, populated_db):
    """Verify GET /api/scans and GET /api/scans/{id} return execution history."""
    res = client.get("/api/scans")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 3
    scan_id = data["items"][0]["id"]

    # Get single scan run
    res_single = client.get(f"/api/scans/{scan_id}")
    assert res_single.status_code == 200
    run_data = res_single.json()
    assert run_data["id"] == scan_id
    assert run_data["status"] == "COMPLETED"

    # Test 404 for non-existent scan run
    res_404 = client.get("/api/scans/999999")
    assert res_404.status_code == 404


def test_post_manual_scan_and_conflict(client, populated_db):
    """Verify POST /api/scan triggers manual scan and returns 409 when scan is already in progress."""
    with patch("app.scanner.scheduler.ScanScheduler.trigger_manual_scan") as mock_scan:
        mock_scan.return_value = {
            "status": "COMPLETED",
            "total_files": 3,
            "new_count": 0,
            "existing_count": 3,
            "removed_count": 0,
            "duplicate_count": 2,
        }
        res = client.post("/api/scan")
        assert res.status_code == 200
        assert res.json()["status"] == "COMPLETED"

    # Test conflict 409
    _scan_lock.acquire()
    try:
        res_conflict = client.post("/api/scan")
        assert res_conflict.status_code == 409
        assert "already in progress" in res_conflict.json()["detail"]
    finally:
        _scan_lock.release()


def test_export_files_and_duplicates(client, populated_db):
    """Verify CSV and JSON export endpoints for files and duplicates."""
    # CSV export files
    res_csv = client.get("/api/export/files?format=csv")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "File Name" in res_csv.text
    assert "report1.pdf" in res_csv.text

    # JSON export files
    res_json = client.get("/api/export/files?format=json")
    assert res_json.status_code == 200
    assert isinstance(res_json.json(), list)
    assert len(res_json.json()) >= 4

    # CSV export duplicates
    res_dup_csv = client.get("/api/export/duplicates?format=csv")
    assert res_dup_csv.status_code == 200
    assert "text/csv" in res_dup_csv.headers["content-type"]
    assert "Copies Count" in res_dup_csv.text

    # JSON export duplicates
    res_dup_json = client.get("/api/export/duplicates?format=json")
    assert res_dup_json.status_code == 200
    assert isinstance(res_dup_json.json(), list)
    assert len(res_dup_json.json()) >= 1

