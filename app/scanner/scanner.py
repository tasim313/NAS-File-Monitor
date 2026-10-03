"""Filesystem scanner for recursive directory traversal and file metadata collection.

Collects file name, absolute path, relative path, size, and modified time without reading
file content into memory. Gracefully handles permissions and inaccessible files.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import List, Optional, Union

from app.logging_config import get_logger
from app.scanner.models import ScanDiscoveryResult, ScannedFile

logger = get_logger("app.scanner")


class DirectoryScanner:
    """Scanner for discovering files and extracting filesystem metadata recursively."""

    def __init__(self, base_directory: Union[str, Path], stability_seconds: float = 0.0):
        self.base_directory = Path(base_directory).resolve()
        self.stability_seconds = float(stability_seconds)

    def scan(self) -> ScanDiscoveryResult:
        """Recursively scan base_directory and return discovered files and errors.
        
        Returns:
            ScanDiscoveryResult containing discovered files and any traversal errors.
        """
        discovered_files: List[ScannedFile] = []
        errors: List[dict] = []

        if not self.base_directory.exists():
            error_msg = f"Scan directory does not exist: {self.base_directory}"
            logger.error(error_msg)
            return ScanDiscoveryResult(
                base_directory=str(self.base_directory),
                files=[],
                errors=[{"path": str(self.base_directory), "error": error_msg}],
            )

        if not self.base_directory.is_dir():
            error_msg = f"Target path is not a directory: {self.base_directory}"
            logger.error(error_msg)
            return ScanDiscoveryResult(
                base_directory=str(self.base_directory),
                files=[],
                errors=[{"path": str(self.base_directory), "error": error_msg}],
            )

        logger.info("Starting filesystem scan in: %s", self.base_directory)

        def walk_error_handler(exc: OSError) -> None:
            """Catch permission and I/O errors during os.walk directory descent."""
            err_path = exc.filename or str(self.base_directory)
            err_str = str(exc)
            logger.warning("Directory traversal error at '%s': %s", err_path, err_str)
            errors.append({"path": str(err_path), "error": err_str})

        try:
            for root, dirs, files in os.walk(str(self.base_directory), onerror=walk_error_handler):
                root_path = Path(root)

                for file_name in files:
                    file_path = root_path / file_name

                    try:
                        # Follow symlinks only if target is a regular file; if broken or loop, handle safely
                        st = file_path.stat()

                        # Skip non-regular files (pipes, sockets, block devices, etc.)
                        if not file_path.is_file():
                            continue

                        # Compute relative path from base_directory
                        rel_path = file_path.relative_to(self.base_directory)

                        mtime_utc = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)

                        if self.stability_seconds > 0:
                            age = (datetime.now(timezone.utc) - mtime_utc).total_seconds()
                            if age < self.stability_seconds:
                                logger.debug(
                                    "Skipping file %s due to stability threshold (age: %.2fs < %.2fs)",
                                    file_path, age, self.stability_seconds
                                )
                                continue

                        scanned = ScannedFile(
                            file_name=file_name,
                            file_path=str(file_path.resolve()),
                            relative_path=str(rel_path),
                            file_size=st.st_size,
                            mtime=mtime_utc,
                        )
                        discovered_files.append(scanned)

                    except (PermissionError, FileNotFoundError, OSError) as e:
                        err_str = str(e)
                        logger.warning("Failed to collect metadata for file '%s': %s", file_path, err_str)
                        errors.append({"path": str(file_path), "error": err_str})

        except Exception as e:
            err_str = f"Unexpected error during scan: {e}"
            logger.error(err_str, exc_info=True)
            errors.append({"path": str(self.base_directory), "error": err_str})

        logger.info(
            "Scan discovery complete for %s. Found %d files with %d errors.",
            self.base_directory,
            len(discovered_files),
            len(errors),
        )

        return ScanDiscoveryResult(
            base_directory=str(self.base_directory),
            files=discovered_files,
            errors=errors,
        )


def scan_directory_files(directory: Union[str, Path], stability_seconds: float = 0.0) -> ScanDiscoveryResult:
    """Convenience helper to scan a directory and return discovery results."""
    scanner = DirectoryScanner(directory, stability_seconds=stability_seconds)
    return scanner.scan()

