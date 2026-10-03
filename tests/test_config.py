"""Tests for configuration settings."""

from pathlib import Path

from app.config import Settings


def test_default_config():
    """Verify default configuration values meet project specifications."""
    settings = Settings()

    assert str(settings.NAS_DIRECTORY) == "/media/requisition/Report"
    assert "nas_monitor.db" in settings.DATABASE_URL
    assert settings.SCAN_INTERVAL == 60
    assert settings.HASH_ALGORITHM == "sha256"
    assert settings.FILE_STABILITY_SECONDS == 5
    assert settings.APP_PORT == 8000


def test_custom_config_override():
    """Verify settings can be overridden via constructor or environment variables."""
    custom_settings = Settings(
        NAS_DIRECTORY=Path("/custom/nas/path"),
        SCAN_INTERVAL=120,
        FILE_STABILITY_SECONDS=10,
    )

    assert str(custom_settings.NAS_DIRECTORY) == "/custom/nas/path"
    assert custom_settings.SCAN_INTERVAL == 120
    assert custom_settings.FILE_STABILITY_SECONDS == 10


def test_database_url_resolved():
    """Verify relative sqlite URL resolves to absolute path."""
    settings = Settings()
    resolved = settings.get_database_url_resolved()
    assert resolved.startswith("sqlite:///")
    assert "data/nas_monitor.db" in resolved
