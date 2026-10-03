"""Scan service orchestrating full directory scans, change detection, and scan runs history."""

from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Set, Union

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import File, FileEvent, ScanRun, utc_now
from app.logging_config import get_logger
from app.scanner.hasher import calculate_sha256_safe
from app.scanner.models import ScannedFile
from app.scanner.scanner import DirectoryScanner
from app.services.duplicate_service import DuplicateService
from app.services.file_service import FileService

logger = get_logger("app.scanner")

# Global scan lock ensuring single scan execution at a time
_scan_lock = threading.Lock()


class ScanInProgressError(Exception):
    """Raised when a scan is requested while another scan is already in progress."""
    pass


class ScanService:
    """Orchestrates filesystem scanning, change detection, history recording, and reporting."""

    @staticmethod
    def is_scan_running() -> bool:
        """Check if a scan is currently executing."""
        return _scan_lock.locked()

    @classmethod
    def scan_directory(
        cls,
        db: Session,
        directory: Optional[Union[str, Path]] = None,
        enforce_lock: bool = True,
    ) -> Dict[str, Any]:
        """Execute a full scan of the monitored directory, update database, and return summary.
        
        Args:
            db: Database session.
            directory: Directory to scan (defaults to configured NAS_DIRECTORY).
            enforce_lock: If True, uses the global scan lock to prevent concurrent runs.
            
        Returns:
            Structured dictionary summary of the scan results.
        """
        settings = get_settings()
        target_dir = Path(directory or settings.NAS_DIRECTORY).resolve()
        scan_time = utc_now()

        # Concurrency protection
        if enforce_lock:
            acquired = _scan_lock.acquire(blocking=False)
            if not acquired:
                raise ScanInProgressError("A file scan is already in progress.")
        else:
            acquired = False

        # Initialize ScanRun record
        scan_run = ScanRun(
            started_at=scan_time,
            status="RUNNING",
        )
        db.add(scan_run)
        db.commit()
        db.refresh(scan_run)

        errors: List[Dict[str, str]] = []
        new_files_list: List[File] = []
        existing_files_list: List[File] = []
        modified_files_list: List[File] = []
        removed_files_list: List[File] = []
        restored_files_list: List[File] = []

        try:
            # 1. Check directory existence and accessibility
            if not target_dir.exists() or not target_dir.is_dir():
                err_msg = f"NAS directory unavailable: {target_dir}"
                logger.error(err_msg)
                scan_run.status = "DEGRADED"
                scan_run.completed_at = utc_now()
                scan_run.error_count = 1
                scan_run.error_message = err_msg
                db.commit()
                return {
                    "scan_id": scan_run.id,
                    "scan_time": scan_time.isoformat(),
                    "status": "DEGRADED",
                    "total_files": 0,
                    "new_files": [],
                    "existing_files": [],
                    "modified_files": [],
                    "removed_files": [],
                    "restored_files": [],
                    "duplicate_groups": [],
                    "errors": [{"path": str(target_dir), "error": err_msg}],
                }

            # 2. Filesystem Traversal
            scanner = DirectoryScanner(target_dir)
            discovery = scanner.scan()
            errors.extend(discovery.errors)

            # Map existing database records by path
            db_files_map: Dict[str, File] = {
                f.file_path: f for f in db.scalars(select(File)).all()
            }
            scanned_paths: Set[str] = set()

            # Process discovered files
            for scanned in discovery.files:
                scanned_paths.add(scanned.file_path)

                if scanned.file_path not in db_files_map:
                    # Case 1: Brand new file (NEW / ADDED)
                    sha256, hash_err = calculate_sha256_safe(scanned.file_path)
                    if hash_err or not sha256:
                        errors.append({"path": scanned.file_path, "error": f"Hash failed: {hash_err}"})
                        continue

                    new_file, _ = FileService.register_new_file(
                        db=db,
                        scanned=scanned,
                        sha256=sha256,
                        detection_time=scan_time,
                    )
                    new_files_list.append(new_file)
                    db_files_map[scanned.file_path] = new_file

                else:
                    db_file = db_files_map[scanned.file_path]

                    if not db_file.is_present:
                        # Case 2: Restored file (Scenario D)
                        sha256, hash_err = calculate_sha256_safe(scanned.file_path)
                        if hash_err or not sha256:
                            errors.append({"path": scanned.file_path, "error": f"Restore hash failed: {hash_err}"})
                            continue

                        restored_file, _ = FileService.restore_removed_file(
                            db=db,
                            file_record=db_file,
                            scanned=scanned,
                            sha256=sha256,
                            restore_time=scan_time,
                        )
                        restored_files_list.append(restored_file)

                    else:
                        # File is present. Check if content changed (Scenario E: MODIFIED)
                        # Optimization: if size and mtime are identical, reuse existing hash
                        size_changed = scanned.file_size != db_file.file_size
                        mtime_changed = (
                            db_file.mtime is None
                            or abs((scanned.mtime - db_file.mtime).total_seconds()) > 1.0
                        )

                        if size_changed or mtime_changed:
                            # Re-hash to verify if content actually changed
                            new_sha256, hash_err = calculate_sha256_safe(scanned.file_path)
                            if hash_err or not new_sha256:
                                errors.append({"path": scanned.file_path, "error": f"Re-hash failed: {hash_err}"})
                                continue

                            if new_sha256 != db_file.sha256:
                                # Content changed -> MODIFIED
                                old_hash = db_file.sha256
                                db_file.sha256 = new_sha256
                                db_file.file_size = scanned.file_size
                                db_file.mtime = scanned.mtime
                                db_file.last_seen_at = scan_time
                                db_file.updated_at = scan_time
                                db_file.status = "EXISTING"

                                mod_event = FileEvent(
                                    file_id=db_file.id,
                                    event_type="MODIFIED",
                                    event_time=scan_time,
                                    file_path=db_file.file_path,
                                    file_name=db_file.file_name,
                                    sha256=new_sha256,
                                    file_size=scanned.file_size,
                                    details=f"File content modified. Previous SHA-256: {old_hash}",
                                )
                                db.add(mod_event)
                                modified_files_list.append(db_file)
                            else:
                                # Content identical, only timestamp was touched
                                updated_existing = FileService.update_existing_file(
                                    db=db,
                                    file_record=db_file,
                                    scanned=scanned,
                                    scan_time=scan_time,
                                )
                                existing_files_list.append(updated_existing)
                        else:
                            # Unchanged file -> EXISTING
                            updated_existing = FileService.update_existing_file(
                                db=db,
                                file_record=db_file,
                                scanned=scanned,
                                scan_time=scan_time,
                            )
                            existing_files_list.append(updated_existing)

            # Detect removed files (previously present, now absent)
            for file_path, db_file in db_files_map.items():
                if db_file.is_present and file_path not in scanned_paths:
                    if not Path(file_path).exists():
                        rem_file, _ = FileService.mark_file_as_removed(
                            db=db,
                            file_record=db_file,
                            removal_time=scan_time,
                        )
                        removed_files_list.append(rem_file)

            # Synchronize duplicates
            dup_groups = DuplicateService.synchronize_duplicates(db=db, sync_time=scan_time)
            dup_files_count = DuplicateService.count_duplicate_files(db)

            # Update ScanRun metrics
            completed_time = utc_now()
            scan_run.completed_at = completed_time
            scan_run.status = "COMPLETED"
            scan_run.total_files = len(discovery.files)
            scan_run.new_files = len(new_files_list)
            scan_run.existing_files = len(existing_files_list) + len(modified_files_list)
            scan_run.removed_files = len(removed_files_list)
            scan_run.duplicate_files = dup_files_count
            scan_run.error_count = len(errors)
            if errors:
                scan_run.error_message = f"{len(errors)} error(s) encountered during scan"

            db.commit()

            # Broadcast real-time update to all active WebSocket clients
            try:
                from app.websocket_manager import ws_manager, build_dashboard_payload
                payload = build_dashboard_payload(db, scan_id=scan_run.id)
                ws_manager.broadcast_sync({
                    "type": "SCAN_COMPLETED",
                    "scan_id": scan_run.id,
                    "new_count": len(new_files_list),
                    "total_count": scan_run.total_files,
                    "duplicate_count": dup_files_count,
                    "data": payload,
                })
            except Exception as broadcast_err:
                logger.warning("Failed to broadcast scan update via WebSocket: %s", broadcast_err)

            logger.info(
                "Scan #%d completed: Total=%d, New=%d, Existing=%d, Modified=%d, Removed=%d, Duplicates=%d, Errors=%d",
                scan_run.id,
                scan_run.total_files,
                scan_run.new_files,
                scan_run.existing_files,
                len(modified_files_list),
                scan_run.removed_files,
                scan_run.duplicate_files,
                scan_run.error_count,
            )

            return {
                "scan_id": scan_run.id,
                "scan_time": scan_time.isoformat(),
                "completed_at": completed_time.isoformat(),
                "status": "COMPLETED",
                "total_files": scan_run.total_files,
                "new_files": [f.to_dict() for f in new_files_list],
                "existing_files": [f.to_dict() for f in existing_files_list],
                "modified_files": [f.to_dict() for f in modified_files_list],
                "removed_files": [f.to_dict() for f in removed_files_list],
                "restored_files": [f.to_dict() for f in restored_files_list],
                "duplicate_groups": [g.to_dict() for g in dup_groups],
                "duplicate_files_count": dup_files_count,
                "errors": errors,
            }

        except Exception as e:
            db.rollback()
            err_msg = f"Fatal scan error: {e}"
            logger.error(err_msg, exc_info=True)
            scan_run.completed_at = utc_now()
            scan_run.status = "FAILED"
            scan_run.error_count = len(errors) + 1
            scan_run.error_message = err_msg
            db.commit()
            raise

        finally:
            if acquired:
                _scan_lock.release()

    @staticmethod
    def get_scan_runs(
        db: Session,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScanRun]:
        """Query scan executions sorted by start time descending."""
        stmt = select(ScanRun).order_by(desc(ScanRun.started_at)).limit(limit).offset(offset)
        return list(db.scalars(stmt).all())

    @staticmethod
    def get_scan_run_by_id(db: Session, scan_id: int) -> Optional[ScanRun]:
        """Query a single scan run by primary key ID."""
        return db.scalar(select(ScanRun).where(ScanRun.id == scan_id))

    @staticmethod
    def get_latest_scan_run(db: Session) -> Optional[ScanRun]:
        """Query the most recent scan run."""
        stmt = select(ScanRun).order_by(desc(ScanRun.started_at)).limit(1)
        return db.scalar(stmt)
