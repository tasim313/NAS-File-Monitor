"""Tests for Phases 13-21: Web UI views and templates."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.database.database import get_db
from app.database.models import Base
from app.main import app
from app.services.scan_service import ScanService


@pytest.fixture
def db_session(tmp_path):
    """Fixture providing a fresh isolated SQLite DB session."""
    db_file = tmp_path / "test_web.db"
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
def populated_web_db(db_session, tmp_path):
    """Populate test database with sample scanned records."""
    nas_dir = tmp_path / "nas_web"
    nas_dir.mkdir(parents=True, exist_ok=True)

    f1 = nas_dir / "invoice_1.pdf"
    f2 = nas_dir / "invoice_copy.pdf"
    f3 = nas_dir / "temp_to_delete.txt"

    f1.write_bytes(b"INVOICE_IDENTICAL_CONTENT")
    f2.write_bytes(b"INVOICE_IDENTICAL_CONTENT")
    f3.write_bytes(b"TEMP_CONTENT")

    # Initial scan
    ScanService.scan_directory(db_session, directory=nas_dir, enforce_lock=False)

    # Remove f3 and do second scan
    f3.unlink()
    ScanService.scan_directory(db_session, directory=nas_dir, enforce_lock=False)

    # Add brand new file
    f4 = nas_dir / "new_record.log"
    f4.write_text("log line 1")
    ScanService.scan_directory(db_session, directory=nas_dir, enforce_lock=False)

    return {"dir": nas_dir, "db": db_session}


def test_dashboard_view(client, populated_web_db):
    """Verify GET / and GET /dashboard render HTML with statistics."""
    for path in ["/", "/dashboard"]:
        res = client.get(path)
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "System Dashboard" in res.text
        assert "Newly Added" in res.text
        assert "Previously Added" in res.text
        assert "Content Duplicates" in res.text
        assert "SCAN NOW" in res.text


def test_new_files_view(client, populated_web_db):
    """Verify GET /files/new renders Newly Added Files (List 1)."""
    res = client.get("/files/new")
    assert res.status_code == 200
    assert "Newly Added Files (List 1)" in res.text
    assert "new_record.log" in res.text


def test_previous_files_view(client, populated_web_db):
    """Verify GET /files/previous renders Previously Added Files (List 2)."""
    res = client.get("/files/previous")
    assert res.status_code == 200
    assert "Previously Added Files (List 2)" in res.text
    assert "invoice_1.pdf" in res.text


def test_duplicates_view(client, populated_web_db):
    """Verify GET /files/duplicates renders Duplicate Groups (List 3)."""
    res = client.get("/files/duplicates")
    assert res.status_code == 200
    assert "Content Duplicate Files (List 3)" in res.text
    assert "2 Copies" in res.text
    assert "Original / First Seen Copy" in res.text
    assert "Most Recent / Latest Copy" in res.text


def test_removed_files_view(client, populated_web_db):
    """Verify GET /files/removed renders Removed Files Ledger."""
    res = client.get("/files/removed")
    assert res.status_code == 200
    assert "Removed Files Ledger" in res.text
    assert "temp_to_delete.txt" in res.text


def test_events_view(client, populated_web_db):
    """Verify GET /events renders Activity & Audit Ledger."""
    res = client.get("/events")
    assert res.status_code == 200
    assert "File Activity & Audit Ledger" in res.text
    assert "ADDED" in res.text

    # Test filtering by event_type
    res_filtered = client.get("/events?event_type=REMOVED")
    assert res_filtered.status_code == 200
    assert "REMOVED" in res_filtered.text


def test_scans_view(client, populated_web_db):
    """Verify GET /scans renders Scan Execution History."""
    res = client.get("/scans")
    assert res.status_code == 200
    assert "Scan Execution History" in res.text
    assert "Scan #" in res.text


def test_search_view(client, populated_web_db):
    """Verify GET /search renders search form and filters."""
    res = client.get("/search?q=invoice")
    assert res.status_code == 200
    assert "Global File Registry Search" in res.text
    assert "invoice_1.pdf" in res.text


def test_file_detail_view(client, populated_web_db):
    """Verify GET /files/{id} renders file details and timeline."""
    # Find a valid file ID from the API
    res_api = client.get("/api/files?query=invoice_1.pdf")
    file_id = res_api.json()["items"][0]["id"]

    res = client.get(f"/files/{file_id}")
    assert res.status_code == 200
    assert "File Metadata" in res.text
    assert "Complete Event History Timeline" in res.text
    assert "invoice_1.pdf" in res.text

    # Test 404 for missing file
    res_404 = client.get("/files/999999")
    assert res_404.status_code == 404


def test_api_documents_view(client, populated_web_db):
    """Verify GET /api-docs renders API documentation page with author in footer."""
    res = client.get("/api-docs")
    assert res.status_code == 200
    assert "REST API Documentation & Reference" in res.text
    assert "Swagger UI" in res.text
    assert "ReDoc" in res.text
    assert "Mostasim Mahmud Tasim" in res.text
    assert "ApiDocuments" in res.text

