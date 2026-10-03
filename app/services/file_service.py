"""File service for managing File records, queries, and lifecycle events."""

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database.models import File, FileEvent, utc_now
from app.scanner.models import ScannedFile


class FileService:
    """Service handling database queries and state changes for files and events."""

    @staticmethod
    def get_file_by_id(db: Session, file_id: int) -> Optional[File]:
        """Retrieve a file by primary key ID."""
        return db.scalar(select(File).where(File.id == file_id))

    @staticmethod
    def get_file_by_path(db: Session, file_path: str) -> Optional[File]:
        """Retrieve a file by its unique absolute path."""
        return db.scalar(select(File).where(File.file_path == file_path))

    @staticmethod
    def register_new_file(
        db: Session,
        scanned: ScannedFile,
        sha256: str,
        detection_time: Optional[datetime] = None,
        details: Optional[str] = None,
    ) -> Tuple[File, FileEvent]:
        """Insert a newly detected file and record its ADDED event.
        
        Args:
            db: Database session.
            scanned: Discovered file metadata.
            sha256: Computed cryptographic content hash.
            detection_time: Timestamp of detection (defaults to UTC now).
            details: Optional notes for the event record.
            
        Returns:
            Tuple of (created File, created FileEvent).
        """
        now = detection_time or utc_now()

        file_record = File(
            file_name=scanned.file_name,
            file_path=scanned.file_path,
            relative_path=scanned.relative_path,
            file_size=scanned.file_size,
            sha256=sha256,
            mtime=scanned.mtime,
            first_seen_at=now,
            last_seen_at=now,
            removed_at=None,
            is_present=True,
            is_duplicate=False,
            status="NEW",
            created_at=now,
            updated_at=now,
        )
        db.add(file_record)
        db.flush()  # Populate file_record.id

        event_record = FileEvent(
            file_id=file_record.id,
            event_type="ADDED",
            event_time=now,
            file_path=file_record.file_path,
            file_name=file_record.file_name,
            sha256=sha256,
            file_size=scanned.file_size,
            details=details or "Initial detection: new file added to monitored directory",
        )
        db.add(event_record)
        db.flush()

        return file_record, event_record

    @staticmethod
    def get_newly_added_files(
        db: Session,
        limit: int = 100,
        offset: int = 0,
    ) -> List[File]:
        """Query files currently categorized as NEW, sorted by first_seen_at descending."""
        stmt = (
            select(File)
            .where(File.status == "NEW", File.is_present.is_(True))
            .order_by(desc(File.first_seen_at))
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def count_newly_added_files(db: Session) -> int:
        """Count total files currently categorized as NEW."""
        from sqlalchemy import func
        return db.scalar(
            select(func.count(File.id)).where(File.status == "NEW", File.is_present.is_(True))
        ) or 0

    @staticmethod
    def update_existing_file(
        db: Session,
        file_record: File,
        scanned: ScannedFile,
        scan_time: Optional[datetime] = None,
    ) -> File:
        """Update an existing present file with latest seen timestamp and EXISTING status.
        
        Args:
            db: Database session.
            file_record: Existing File ORM model.
            scanned: Current scanned metadata.
            scan_time: Timestamp of current scan (UTC).
            
        Returns:
            Updated File instance.
        """
        now = scan_time or utc_now()
        file_record.last_seen_at = now
        file_record.updated_at = now
        file_record.is_present = True

        # Transition status from NEW to EXISTING for subsequent scans
        if file_record.status == "NEW":
            file_record.status = "EXISTING"

        # Update mtime if slightly refreshed
        if scanned.mtime:
            file_record.mtime = scanned.mtime

        db.flush()
        return file_record

    @staticmethod
    def get_previously_added_files(
        db: Session,
        limit: int = 100,
        offset: int = 0,
    ) -> List[File]:
        """Query files detected in previous scans (status EXISTING and present)."""
        stmt = (
            select(File)
            .where(File.status == "EXISTING", File.is_present.is_(True))
            .order_by(desc(File.last_seen_at))
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def count_previously_added_files(db: Session) -> int:
        """Count total files categorized as EXISTING and currently present."""
        from sqlalchemy import func
        return db.scalar(
            select(func.count(File.id)).where(File.status == "EXISTING", File.is_present.is_(True))
        ) or 0

    @staticmethod
    def mark_file_as_removed(
        db: Session,
        file_record: File,
        removal_time: Optional[datetime] = None,
        details: Optional[str] = None,
    ) -> Tuple[File, FileEvent]:
        """Mark a file as REMOVED, record removed_at, and create a REMOVED event.
        
        Preserves all historical fields and does not delete the database row.
        """
        now = removal_time or utc_now()
        file_record.is_present = False
        file_record.status = "REMOVED"
        file_record.removed_at = now
        file_record.updated_at = now

        event_record = FileEvent(
            file_id=file_record.id,
            event_type="REMOVED",
            event_time=now,
            file_path=file_record.file_path,
            file_name=file_record.file_name,
            sha256=file_record.sha256,
            file_size=file_record.file_size,
            details=details or "File no longer present in monitored directory during scan",
        )
        db.add(event_record)
        db.flush()

        return file_record, event_record

    @staticmethod
    def restore_removed_file(
        db: Session,
        file_record: File,
        scanned: ScannedFile,
        sha256: str,
        restore_time: Optional[datetime] = None,
    ) -> Tuple[File, FileEvent]:
        """Restore a previously REMOVED file that has reappeared at the same path.
        
        Preserves original first_seen_at and creates a RESTORED event.
        """
        now = restore_time or utc_now()
        file_record.is_present = True
        file_record.removed_at = None
        file_record.status = "EXISTING"
        file_record.last_seen_at = now
        file_record.updated_at = now
        file_record.file_size = scanned.file_size
        file_record.sha256 = sha256
        file_record.mtime = scanned.mtime

        event_record = FileEvent(
            file_id=file_record.id,
            event_type="RESTORED",
            event_time=now,
            file_path=file_record.file_path,
            file_name=file_record.file_name,
            sha256=sha256,
            file_size=scanned.file_size,
            details="Previously removed file returned to monitored directory",
        )
        db.add(event_record)
        db.flush()

        return file_record, event_record

    @staticmethod
    def get_removed_files(
        db: Session,
        limit: int = 100,
        offset: int = 0,
    ) -> List[File]:
        """Query historical files marked as REMOVED, sorted by removed_at descending."""
        stmt = (
            select(File)
            .where(File.is_present.is_(False))
            .order_by(desc(File.removed_at))
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def count_removed_files(db: Session) -> int:
        """Count total files currently marked as REMOVED."""
        from sqlalchemy import func
        return db.scalar(
            select(func.count(File.id)).where(File.is_present.is_(False))
        ) or 0



    @staticmethod
    def get_file_events(
        db: Session,
        file_id: Optional[int] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[FileEvent]:
        """Query historical file events with optional filters."""
        stmt = select(FileEvent)
        if file_id is not None:
            stmt = stmt.where(FileEvent.file_id == file_id)
        if event_type is not None:
            stmt = stmt.where(FileEvent.event_type == event_type)
        stmt = stmt.order_by(desc(FileEvent.event_time)).limit(limit).offset(offset)
        return list(db.scalars(stmt).all())
