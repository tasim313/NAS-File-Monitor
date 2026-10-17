"""Application configuration using Pydantic Settings.

Reads settings from environment variables or .env file with production defaults.
"""

from functools import lru_cache
import os
from pathlib import Path
from typing import List, Literal, Tuple, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration settings for NAS File Monitoring System."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base project directory
    PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

    # NAS Monitoring Target Directory
    NAS_DIRECTORY: Path = Field(
        default=Path("/media/requisition/Report"),
        description="Path to the NAS directory to monitor",
    )

    # Database
    DATABASE_URL: str = Field(
        default="sqlite:///data/nas_monitor.db",
        description="SQLAlchemy database connection string",
    )

    # Scanner settings
    SCAN_INTERVAL: int = Field(
        default=60,
        ge=1,
        description="Interval in seconds between periodic scans",
    )
    HASH_ALGORITHM: Literal["sha256", "sha512"] = Field(
        default="sha256",
        description="Hashing algorithm for file duplicate detection",
    )
    FILE_STABILITY_SECONDS: int = Field(
        default=5,
        ge=0,
        description="Seconds file must remain unchanged before final hash calculation",
    )
    CHUNK_SIZE_BYTES: int = Field(
        default=1024 * 1024,
        ge=4096,
        description="Buffer chunk size in bytes for streaming SHA-256 calculation (default: 1MB)",
    )

    # Logging & Storage
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Application logging level",
    )
    LOG_DIR: Path = Field(
        default=Path("logs"),
        description="Directory for application log files",
    )
    DATA_DIR: Path = Field(
        default=Path("data"),
        description="Directory for data files including SQLite database",
    )

    # Server settings
    APP_HOST: str = Field(
        default="0.0.0.0",
        description="Host interface to bind the application server",
    )
    APP_PORT: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Port for the application server",
    )
    ALLOWED_HOSTS: Union[str, List[str]] = Field(
        default=["*"],
        description="Allowed host header names or IP addresses",
    )
    CORS_ORIGINS: Union[str, List[str]] = Field(
        default=["*"],
        description="Allowed CORS origins",
    )

    # Brother MFC-L6900DW Printer & Scanner Settings
    PRINTER_ENABLED: bool = Field(
        default=True,
        description="Enable Brother printer and scanner integration",
    )
    PRINTER_HOST: str = Field(
        default="192.168.4.171",
        description="IP address or hostname of Brother MFC-L6900DW",
    )
    PRINTER_PASSWORD: str = Field(
        default="initpass",
        description="Web management admin password for Brother printer",
    )
    PRINTER_POLL_INTERVAL: int = Field(
        default=120,
        ge=10,
        description="Interval in seconds for polling printer status and counters",
    )
    PRINTER_SYSLOG_ENABLED: bool = Field(
        default=True,
        description="Enable UDP Syslog listener for real-time printer audit events",
    )
    PRINTER_SYSLOG_HOST: str = Field(
        default="0.0.0.0",
        description="Interface to bind Syslog UDP receiver",
    )
    PRINTER_SYSLOG_PORT: int = Field(
        default=1514,
        ge=1024,
        le=65535,
        description="UDP port for Brother Syslog audit messages",
    )

    # OpenLDAP Settings
    LDAP_ENABLED: bool = Field(
        default=True,
        description="Enable OpenLDAP user synchronization and authentication lookup",
    )
    LDAP_SERVER: str = Field(
        default="192.168.1.19",
        description="OpenLDAP server host or IP",
    )
    LDAP_PORT: int = Field(
        default=389,
        ge=1,
        le=65535,
        description="OpenLDAP server port",
    )
    LDAP_BASE_DN: str = Field(
        default="dc=aikhanlab,dc=com",
        description="Search base DN for OpenLDAP directory",
    )

    @field_validator("LOG_DIR", "DATA_DIR", mode="after")
    @classmethod
    def resolve_relative_dir(cls, v: Path) -> Path:
        """Resolve relative directory paths against the project root and create them."""
        project_root = Path(__file__).resolve().parent.parent
        resolved = v if v.is_absolute() else (project_root / v)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    @field_validator("NAS_DIRECTORY", mode="after")
    @classmethod
    def resolve_nas_directory(cls, v: Path) -> Path:
        """Ensure NAS directory is represented as an absolute Path."""
        return v.resolve()

    def get_database_url_resolved(self) -> str:
        """Resolve sqlite:/// relative database path to absolute project path."""
        if self.DATABASE_URL.startswith("sqlite:///") and not self.DATABASE_URL.startswith("sqlite:////"):
            rel_path = self.DATABASE_URL.replace("sqlite:///", "")
            abs_path = (self.PROJECT_ROOT / rel_path).resolve()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{abs_path}"
        return self.DATABASE_URL

    def validate_nas_directory(self) -> Tuple[bool, str]:
        """Check if NAS directory exists, is a valid directory, and is readable."""
        if not self.NAS_DIRECTORY.exists():
            return False, f"Directory does not exist: {self.NAS_DIRECTORY}"
        if not self.NAS_DIRECTORY.is_dir():
            return False, f"Path is not a directory: {self.NAS_DIRECTORY}"
        if not os.access(self.NAS_DIRECTORY, os.R_OK):
            return False, f"Permission denied (unreadable): {self.NAS_DIRECTORY}"
        return True, "Directory accessible and readable"

    @property
    def is_nas_available(self) -> bool:
        """Convenience property indicating whether the NAS directory is currently available."""
        valid, _ = self.validate_nas_directory()
        return valid

    def get_allowed_hosts(self) -> List[str]:
        """Return allowed hosts as a list of strings."""
        if isinstance(self.ALLOWED_HOSTS, list):
            return self.ALLOWED_HOSTS
        return [h.strip() for h in str(self.ALLOWED_HOSTS).split(",") if h.strip()]

    def get_cors_origins(self) -> List[str]:
        """Return allowed CORS origins as a list of strings."""
        if isinstance(self.CORS_ORIGINS, list):
            return self.CORS_ORIGINS
        return [o.strip() for o in str(self.CORS_ORIGINS).split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton instance of the application settings."""
    return Settings()


def reload_settings() -> Settings:
    """Clear settings cache and return freshly loaded settings."""
    get_settings.cache_clear()
    return get_settings()
