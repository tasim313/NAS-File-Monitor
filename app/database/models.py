"""SQLAlchemy database models for NAS File Monitoring System.

Defines tables for files, file events, scan runs, duplicate groups, and duplicate members.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.datetime_util import format_dhaka_12h


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime type that guarantees timezone-aware UTC datetime for SQLite."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value



class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy models."""
    pass


class File(Base):
    """Represents a monitored file record with current status, metadata, and timestamps."""

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True, index=True)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mtime: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    removed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )

    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NEW", index=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    events: Mapped[List["FileEvent"]] = relationship(
        "FileEvent", back_populates="file", cascade="all, delete-orphan", order_by="desc(FileEvent.event_time)"
    )
    duplicate_memberships: Mapped[List["DuplicateMember"]] = relationship(
        "DuplicateMember", back_populates="file", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_files_is_present_duplicate", "is_present", "is_duplicate"),
        Index("ix_files_sha256_present", "sha256", "is_present"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert File instance to dictionary."""
        return {
            "id": self.id,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "mtime": self.mtime.isoformat() if self.mtime else None,
            "mtime_dhaka": format_dhaka_12h(self.mtime) if self.mtime else None,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "first_seen_at_dhaka": format_dhaka_12h(self.first_seen_at) if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "last_seen_at_dhaka": format_dhaka_12h(self.last_seen_at) if self.last_seen_at else None,
            "removed_at": self.removed_at.isoformat() if self.removed_at else None,
            "removed_at_dhaka": format_dhaka_12h(self.removed_at) if self.removed_at else None,
            "is_present": self.is_present,
            "is_duplicate": self.is_duplicate,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FileEvent(Base):
    """Historical timeline event for file lifecycle changes (ADDED, SEEN, REMOVED, etc.)."""

    __tablename__ = "file_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_time: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    file: Mapped[Optional["File"]] = relationship("File", back_populates="events")

    __table_args__ = (
        Index("ix_file_events_type_time", "event_type", "event_time"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert FileEvent instance to dictionary."""
        file_mtime = self.file.mtime if self.file else None
        return {
            "id": self.id,
            "file_id": self.file_id,
            "event_type": self.event_type,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "event_time_dhaka": format_dhaka_12h(self.event_time) if self.event_time else None,
            "file_mtime": file_mtime.isoformat() if file_mtime else None,
            "file_mtime_dhaka": format_dhaka_12h(file_mtime) if file_mtime else None,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "file_size": self.file_size,
            "details": self.details,
        }


class ScanRun(Base):
    """Tracks each scan execution with run times, metrics, and error state."""

    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING", index=True)
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    existing_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    removed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert ScanRun instance to dictionary."""
        duration = None
        if self.started_at and self.completed_at:
            duration = max(0.0, (self.completed_at - self.started_at).total_seconds())

        return {
            "id": self.id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "start_time": self.started_at.isoformat() if self.started_at else None,
            "end_time": self.completed_at.isoformat() if self.completed_at else None,
            "started_at_dhaka": format_dhaka_12h(self.started_at) if self.started_at else None,
            "completed_at_dhaka": format_dhaka_12h(self.completed_at) if self.completed_at else None,
            "duration_seconds": duration,
            "trigger_type": "SCHEDULED",
            "status": self.status,
            "total_files": self.total_files,
            "new_files": self.new_files,
            "new_files_count": self.new_files,
            "existing_files": self.existing_files,
            "existing_files_count": self.existing_files,
            "removed_files": self.removed_files,
            "removed_files_count": self.removed_files,
            "duplicate_files": self.duplicate_files,
            "duplicate_files_count": self.duplicate_files,
            "modified_files_count": 0,
            "error_count": self.error_count,
            "error_message": self.error_message,
        }


class DuplicateGroup(Base):
    """Group of duplicate files sharing the identical SHA-256 hash."""

    __tablename__ = "duplicate_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    first_file_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    latest_file_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    first_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    latest_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    first_file: Mapped[Optional["File"]] = relationship(
        "File", foreign_keys=[first_file_id], post_update=True
    )
    latest_file: Mapped[Optional["File"]] = relationship(
        "File", foreign_keys=[latest_file_id], post_update=True
    )
    members: Mapped[List["DuplicateMember"]] = relationship(
        "DuplicateMember",
        back_populates="duplicate_group",
        cascade="all, delete-orphan",
        order_by="DuplicateMember.first_seen_at",
    )

    @property
    def file_size(self) -> Optional[int]:
        """Return file size of group members."""
        if self.first_file and self.first_file.file_size is not None:
            return self.first_file.file_size
        if self.latest_file and self.latest_file.file_size is not None:
            return self.latest_file.file_size
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert DuplicateGroup instance to dictionary."""
        return {
            "id": self.id,
            "sha256": self.sha256,
            "first_file_id": self.first_file_id,
            "latest_file_id": self.latest_file_id,
            "duplicate_count": self.duplicate_count,
            "file_size": self.file_size,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "first_seen_at_dhaka": format_dhaka_12h(self.first_seen_at) if self.first_seen_at else None,
            "latest_seen_at": self.latest_seen_at.isoformat() if self.latest_seen_at else None,
            "latest_seen_at_dhaka": format_dhaka_12h(self.latest_seen_at) if self.latest_seen_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }



class DuplicateMember(Base):
    """Membership of a file in a duplicate group with occurrence timestamps."""

    __tablename__ = "duplicate_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    duplicate_group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("duplicate_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    # Relationships
    duplicate_group: Mapped["DuplicateGroup"] = relationship(
        "DuplicateGroup", back_populates="members"
    )
    file: Mapped["File"] = relationship(
        "File", back_populates="duplicate_memberships"
    )

    __table_args__ = (
        UniqueConstraint("duplicate_group_id", "file_id", name="uq_group_file_member"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert DuplicateMember instance to dictionary."""
        return {
            "id": self.id,
            "duplicate_group_id": self.duplicate_group_id,
            "file_id": self.file_id,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "first_seen_at_dhaka": format_dhaka_12h(self.first_seen_at) if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "last_seen_at_dhaka": format_dhaka_12h(self.last_seen_at) if self.last_seen_at else None,
        }


class PrinterDevice(Base):
    """Represents a Brother MFC-L6900DW hardware device status and lifetime counters."""

    __tablename__ = "printer_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="Brother MFC-L6900DW series")
    node_name: Mapped[str] = mapped_column(String(64), nullable=False, default="brw485f99ca71a8")
    serial_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="Ready", index=True)
    toner_level: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    drum_level: Mapped[int] = mapped_column(Integer, nullable=False, default=98)

    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_printed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_copied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_jams: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert PrinterDevice instance to dictionary."""
        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "model_name": self.model_name,
            "node_name": self.node_name,
            "serial_number": self.serial_number or "-",
            "status": self.status,
            "toner_level": self.toner_level,
            "drum_level": self.drum_level,
            "total_pages": self.total_pages,
            "total_printed": self.total_printed,
            "total_scanned": self.total_scanned,
            "total_copied": self.total_copied,
            "total_jams": self.total_jams,
            "last_error": self.last_error or "None",
            "error_history": self.error_history,
            "last_polled_at": self.last_polled_at.isoformat() if self.last_polled_at else None,
            "last_polled_at_dhaka": format_dhaka_12h(self.last_polled_at) if self.last_polled_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_at_dhaka": format_dhaka_12h(self.updated_at) if self.updated_at else None,
        }


class PrinterLog(Base):
    """Audit log for print, scan, copy, and OpenLDAP user access events."""

    __tablename__ = "printer_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PRINT", index=True
    )  # PRINT, SCAN, COPY, LOGIN, LOGOUT, AUTH_FAIL
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    user_department: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    client_pc: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    document_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    document_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="PDF")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    two_sided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS", index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="SYSLOG")
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    __table_args__ = (
        Index("idx_printer_logs_time_type", "timestamp", "event_type"),
        Index("idx_printer_logs_user", "username"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert PrinterLog instance to dictionary with Asia/Dhaka 12-hour formatted timestamp."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "timestamp_dhaka": format_dhaka_12h(self.timestamp) if self.timestamp else None,
            "event_type": self.event_type,
            "username": self.username,
            "user_display_name": self.user_display_name or self.username,
            "user_department": self.user_department or "-",
            "client_pc": self.client_pc or "-",
            "document_name": self.document_name or "-",
            "document_type": self.document_type or "DOCUMENT",
            "page_count": self.page_count,
            "two_sided": self.two_sided,
            "status": self.status,
            "source": self.source,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_at_dhaka": format_dhaka_12h(self.created_at) if self.created_at else None,
        }


class LdapUser(Base):
    """Cached directory user synchronized from OpenLDAP."""

    __tablename__ = "ldap_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    cn: Mapped[str] = mapped_column(String(128), nullable=False)
    mail: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    mobile: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    employee_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    print_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_seen_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert LdapUser instance to dictionary."""
        return {
            "id": self.id,
            "uid": self.uid,
            "cn": self.cn,
            "mail": self.mail or "-",
            "department": self.department or "-",
            "title": self.title or "-",
            "mobile": self.mobile or "-",
            "employee_number": self.employee_number or "-",
            "login_count": self.login_count,
            "print_count": self.print_count,
            "scan_count": self.scan_count,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "last_seen_at_dhaka": format_dhaka_12h(self.last_seen_at) if self.last_seen_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
