"""File content hashing service using streaming chunks.

Safely computes cryptographic content hashes (SHA-256) for files of any size without
loading the full file into memory. Handles read errors gracefully.
"""

import hashlib
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("app.scanner")


class FileHasher:
    """Streams file content in configurable chunks to calculate cryptographic hashes."""

    def __init__(self, chunk_size: Optional[int] = None, algorithm: Optional[str] = None):
        settings = get_settings()
        self.chunk_size = chunk_size or settings.CHUNK_SIZE_BYTES
        self.algorithm = (algorithm or settings.HASH_ALGORITHM).lower()

    def _get_hash_instance(self) -> Any:
        """Create a new hash object for the configured algorithm."""
        if self.algorithm == "sha256":
            return hashlib.sha256()
        elif self.algorithm == "sha512":
            return hashlib.sha512()
        else:
            try:
                return hashlib.new(self.algorithm)
            except ValueError as e:
                logger.error("Unsupported hash algorithm '%s', falling back to sha256: %s", self.algorithm, e)
                return hashlib.sha256()

    def compute_hash(self, file_path: Union[str, Path]) -> str:
        """Compute the content hash of a file by streaming in chunks.
        
        Args:
            file_path: Path to the target file.
            
        Returns:
            Hexadecimal hash digest string.
            
        Raises:
            FileNotFoundError: If the file does not exist.
            PermissionError: If read permission is denied.
            IsADirectoryError: If the path is a directory.
            OSError: On general I/O errors.
        """
        path = Path(file_path).resolve()

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"Target is a directory, not a file: {path}")

        hasher = self._get_hash_instance()

        with open(path, "rb") as f:
            while chunk := f.read(self.chunk_size):
                hasher.update(chunk)

        return hasher.hexdigest()

    def compute_hash_safe(self, file_path: Union[str, Path]) -> Tuple[Optional[str], Optional[str]]:
        """Safely compute hash, returning (hash_string, error_message).
        
        Does not raise exceptions; returns None and an error description on failure.
        """
        try:
            return self.compute_hash(file_path), None
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as e:
            err_msg = str(e)
            logger.warning("Failed to calculate hash for '%s': %s", file_path, err_msg)
            return None, err_msg
        except Exception as e:
            err_msg = f"Unexpected error hashing '{file_path}': {e}"
            logger.error(err_msg, exc_info=True)
            return None, err_msg


def calculate_sha256(file_path: Union[str, Path], chunk_size: Optional[int] = None) -> str:
    """Convenience function to compute SHA-256 for a file using chunked streaming."""
    hasher = FileHasher(chunk_size=chunk_size, algorithm="sha256")
    return hasher.compute_hash(file_path)


def calculate_sha256_safe(
    file_path: Union[str, Path], chunk_size: Optional[int] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Convenience function to safely compute SHA-256 returning (hash, error)."""
    hasher = FileHasher(chunk_size=chunk_size, algorithm="sha256")
    return hasher.compute_hash_safe(file_path)
