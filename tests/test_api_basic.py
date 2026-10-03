"""Tests for FastAPI entry point and health check endpoint."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_check_endpoint():
    """Verify GET /api/health responds with system status."""
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "version" in data
        assert "nas_directory" in data
        assert "nas_available" in data
        assert "database" in data
        assert data["database"] is True
