"""Data transfer models for the filesystem scanner."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List


@dataclass
class ScannedFile:
    """Metadata collected from a file discovered on the filesystem."""

    file_name: str
    file_path: str
    relative_path: str
    file_size: int
    mtime: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Convert ScannedFile to dictionary."""
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "file_size": self.file_size,
            "mtime": self.mtime.isoformat(),
        }


@dataclass
class ScanDiscoveryResult:
    """Aggregate result from a filesystem traversal."""

    base_directory: str
    files: List[ScannedFile]
    errors: List[Dict[str, str]]

    @property
    def total_count(self) -> int:
        """Total number of successfully discovered files."""
        return len(self.files)

    @property
    def error_count(self) -> int:
        """Total number of errors encountered during scan."""
        return len(self.errors)
