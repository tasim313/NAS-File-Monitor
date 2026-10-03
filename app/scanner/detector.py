"""Change detector for comparing filesystem discoveries against the database."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import DuplicateGroup, File, utc_now
from app.logging_config import get_logger
from app.scanner.hasher import calculate_sha256_safe
from app.scanner.models import ScannedFile
from app.services.duplicate_service import DuplicateService
from app.services.file_service import FileService

logger = get_logger("app.scanner")


class ChangeDetector:
    """Detects newly added, existing, removed, restored, and duplicate files."""

    def __init__(self, db: Session):
        self.db = db

    def detect_new_files(
        self,
        scanned_files: List[ScannedFile],
        scan_time: Optional[datetime] = None,
    ) -> Tuple[List[File], List[dict]]:
        """Identify scanned files that do not exist in the database and register them as NEW."""
        now = scan_time or utc_now()
        new_files: List[File] = []
        errors: List[dict] = []

        known_paths = set(self.db.scalars(select(File.file_path)).all())

        for scanned in scanned_files:
            if scanned.file_path not in known_paths:
                logger.info("New file detected: %s (%s)", scanned.file_name, scanned.file_path)

                sha256, err = calculate_sha256_safe(scanned.file_path)
                if err or not sha256:
                    error_entry = {
                        "path": scanned.file_path,
                        "error": f"Failed to compute hash for new file: {err}",
                    }
                    logger.error(error_entry["error"])
                    errors.append(error_entry)
                    continue

                try:
                    file_record, _ = FileService.register_new_file(
                        db=self.db,
                        scanned=scanned,
                        sha256=sha256,
                        detection_time=now,
                    )
                    new_files.append(file_record)
                    known_paths.add(scanned.file_path)
                except Exception as e:
                    err_str = f"Failed to register new file '{scanned.file_path}': {e}"
                    logger.error(err_str, exc_info=True)
                    errors.append({"path": scanned.file_path, "error": err_str})

        return new_files, errors

    def detect_existing_files(
        self,
        scanned_files: List[ScannedFile],
        scan_time: Optional[datetime] = None,
    ) -> List[File]:
        """Identify scanned files that already exist in the database and are present."""
        now = scan_time or utc_now()
        existing_updated: List[File] = []

        stmt = select(File).where(File.is_present.is_(True))
        db_files_by_path: Dict[str, File] = {f.file_path: f for f in self.db.scalars(stmt).all()}

        for scanned in scanned_files:
            if scanned.file_path in db_files_by_path:
                db_file = db_files_by_path[scanned.file_path]
                updated_record = FileService.update_existing_file(
                    db=self.db,
                    file_record=db_file,
                    scanned=scanned,
                    scan_time=now,
                )
                existing_updated.append(updated_record)

        return existing_updated

    def detect_removed_files(
        self,
        scanned_files: List[ScannedFile],
        removal_time: Optional[datetime] = None,
    ) -> List[File]:
        """Identify files previously recorded as present that are absent in current scan."""
        now = removal_time or utc_now()
        removed_files: List[File] = []

        present_db_files = list(self.db.scalars(select(File).where(File.is_present.is_(True))).all())
        scanned_paths_set = {f.file_path for f in scanned_files}

        for db_file in present_db_files:
            if db_file.file_path not in scanned_paths_set:
                if not Path(db_file.file_path).exists():
                    logger.info("Removed file detected: %s (%s)", db_file.file_name, db_file.file_path)
                    updated_file, _ = FileService.mark_file_as_removed(
                        db=self.db,
                        file_record=db_file,
                        removal_time=now,
                    )
                    removed_files.append(updated_file)

        return removed_files

    def detect_restored_files(
        self,
        scanned_files: List[ScannedFile],
        scan_time: Optional[datetime] = None,
    ) -> Tuple[List[File], List[dict]]:
        """Identify files previously marked REMOVED that reappeared at the same path."""
        now = scan_time or utc_now()
        restored_files: List[File] = []
        errors: List[dict] = []

        removed_db_files: Dict[str, File] = {
            f.file_path: f for f in self.db.scalars(select(File).where(File.is_present.is_(False))).all()
        }

        for scanned in scanned_files:
            if scanned.file_path in removed_db_files:
                db_file = removed_db_files[scanned.file_path]
                logger.info("Restored file detected: %s (%s)", scanned.file_name, scanned.file_path)

                sha256, err = calculate_sha256_safe(scanned.file_path)
                if err or not sha256:
                    error_entry = {
                        "path": scanned.file_path,
                        "error": f"Failed to compute hash for restored file: {err}",
                    }
                    logger.error(error_entry["error"])
                    errors.append(error_entry)
                    continue

                try:
                    restored_record, _ = FileService.restore_removed_file(
                        db=self.db,
                        file_record=db_file,
                        scanned=scanned,
                        sha256=sha256,
                        restore_time=now,
                    )
                    restored_files.append(restored_record)
                except Exception as e:
                    err_str = f"Failed to restore file '{scanned.file_path}': {e}"
                    logger.error(err_str, exc_info=True)
                    errors.append({"path": scanned.file_path, "error": err_str})

        return restored_files, errors

    def process_scan(
        self,
        scanned_files: List[ScannedFile],
        scan_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Perform unified classification for all files and update duplicate groupings."""
        now = scan_time or utc_now()

        known_files: Dict[str, File] = {
            f.file_path: f for f in self.db.scalars(select(File)).all()
        }

        new_scans: List[ScannedFile] = []
        existing_scans: List[ScannedFile] = []
        restored_scans: List[ScannedFile] = []

        for scanned in scanned_files:
            if scanned.file_path not in known_files:
                new_scans.append(scanned)
            elif not known_files[scanned.file_path].is_present:
                restored_scans.append(scanned)
            else:
                existing_scans.append(scanned)

        new_files, new_errors = self.detect_new_files(new_scans, scan_time=now)
        restored_files, restore_errors = self.detect_restored_files(restored_scans, scan_time=now)
        existing_files = self.detect_existing_files(existing_scans, scan_time=now)
        removed_files = self.detect_removed_files(scanned_files, removal_time=now)

        duplicate_groups = DuplicateService.synchronize_duplicates(
            db=self.db,
            sync_time=now,
        )

        return {
            "new_files": new_files,
            "existing_files": existing_files,
            "removed_files": removed_files,
            "restored_files": restored_files,
            "duplicate_groups": duplicate_groups,
            "errors": new_errors + restore_errors,
        }
