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
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "removed_at": self.removed_at.isoformat() if self.removed_at else None,
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
        return {
            "id": self.id,
            "file_id": self.file_id,
            "event_type": self.event_type,
            "event_time": self.event_time.isoformat() if self.event_time else None,
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
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
            "total_files": self.total_files,
            "new_files": self.new_files,
            "existing_files": self.existing_files,
            "removed_files": self.removed_files,
            "duplicate_files": self.duplicate_files,
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
            "latest_seen_at": self.latest_seen_at.isoformat() if self.latest_seen_at else None,
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
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }
