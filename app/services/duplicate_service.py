"""Duplicate service for tracking SHA-256 duplicate groups and members."""

from datetime import datetime
from typing import Dict, List, Optional, Set

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, joinedload

from app.database.models import DuplicateGroup, DuplicateMember, File, FileEvent, utc_now
from app.logging_config import get_logger

logger = get_logger("app.scanner")


class DuplicateService:
    """Manages duplicate file groups, first vs latest occurrences, and member relationships."""

    @staticmethod
    def synchronize_duplicates(
        db: Session,
        target_hashes: Optional[Set[str]] = None,
        sync_time: Optional[datetime] = None,
    ) -> List[DuplicateGroup]:
        """Synchronize duplicate groups and file statuses based on SHA-256 content hashes.
        
        Args:
            db: Database session.
            target_hashes: Optional subset of hashes to update; if None, updates all hashes.
            sync_time: Timestamp of sync event (UTC).
            
        Returns:
            List of updated DuplicateGroup instances with duplicate_count > 1.
        """
        now = sync_time or utc_now()
        active_groups: List[DuplicateGroup] = []

        # Find all hashes and their counts among present files
        query = (
            select(File.sha256, func.count(File.id).label("cnt"))
            .where(File.is_present.is_(True))
            .group_by(File.sha256)
        )
        if target_hashes is not None:
            query = query.where(File.sha256.in_(target_hashes))

        hash_counts = db.execute(query).all()

        for sha256_hash, count in hash_counts:
            # Query all present files sharing this hash, ordered chronologically
            files = list(
                db.scalars(
                    select(File)
                    .where(File.sha256 == sha256_hash, File.is_present.is_(True))
                    .order_by(File.first_seen_at.asc(), File.id.asc())
                ).all()
            )

            # Look up existing DuplicateGroup
            group = db.scalar(
                select(DuplicateGroup).where(DuplicateGroup.sha256 == sha256_hash)
            )

            if len(files) >= 2:
                # We have a duplicate group!
                first_file = files[0]
                latest_file = files[-1]

                if not group:
                    group = DuplicateGroup(
                        sha256=sha256_hash,
                        first_file_id=first_file.id,
                        latest_file_id=latest_file.id,
                        duplicate_count=len(files),
                        first_seen_at=first_file.first_seen_at,
                        latest_seen_at=latest_file.first_seen_at,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(group)
                    db.flush()
                    logger.info("New duplicate group created for SHA256 %s (count: %d)", sha256_hash, len(files))
                else:
                    group.first_file_id = first_file.id
                    group.latest_file_id = latest_file.id
                    group.duplicate_count = len(files)
                    group.first_seen_at = first_file.first_seen_at
                    group.latest_seen_at = latest_file.first_seen_at
                    group.updated_at = now
                    db.flush()

                # Synchronize members and file statuses
                existing_member_file_ids = set(
                    db.scalars(
                        select(DuplicateMember.file_id).where(
                            DuplicateMember.duplicate_group_id == group.id
                        )
                    ).all()
                )

                for f in files:
                    was_duplicate = f.is_duplicate
                    f.is_duplicate = True

                    # Record DUPLICATE_DETECTED event on first identification as a duplicate
                    if not was_duplicate:
                        event = FileEvent(
                            file_id=f.id,
                            event_type="DUPLICATE_DETECTED",
                            event_time=now,
                            file_path=f.file_path,
                            file_name=f.file_name,
                            sha256=f.sha256,
                            file_size=f.file_size,
                            details=f"Duplicate content identified in group #{group.id} (first file: {first_file.file_name})",
                        )
                        db.add(event)

                    if f.id not in existing_member_file_ids:
                        member = DuplicateMember(
                            duplicate_group_id=group.id,
                            file_id=f.id,
                            first_seen_at=f.first_seen_at,
                            last_seen_at=f.last_seen_at,
                        )
                        db.add(member)
                    else:
                        # Update member last_seen_at
                        member_record = db.scalar(
                            select(DuplicateMember).where(
                                DuplicateMember.duplicate_group_id == group.id,
                                DuplicateMember.file_id == f.id,
                            )
                        )
                        if member_record:
                            member_record.last_seen_at = f.last_seen_at

                active_groups.append(group)

            elif group and len(files) < 2:
                # Group dropped below 2 present copies
                group.duplicate_count = len(files)
                group.updated_at = now
                if len(files) == 1:
                    files[0].is_duplicate = False

        db.flush()
        return active_groups

    @staticmethod
    def get_duplicate_groups(
        db: Session,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DuplicateGroup]:
        """Query active duplicate groups (duplicate_count >= 2) with first and latest files eager-loaded."""
        stmt = (
            select(DuplicateGroup)
            .options(
                joinedload(DuplicateGroup.first_file),
                joinedload(DuplicateGroup.latest_file),
            )
            .where(DuplicateGroup.duplicate_count >= 2)
            .order_by(desc(DuplicateGroup.latest_seen_at))
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(stmt).unique().all())

    @staticmethod
    def count_duplicate_groups(db: Session) -> int:
        """Count total active duplicate groups with 2 or more present copies."""
        return db.scalar(
            select(func.count(DuplicateGroup.id)).where(DuplicateGroup.duplicate_count >= 2)
        ) or 0

    @staticmethod
    def count_duplicate_files(db: Session) -> int:
        """Count total present files that are duplicates."""
        return db.scalar(
            select(func.count(File.id)).where(File.is_duplicate.is_(True), File.is_present.is_(True))
        ) or 0

    @staticmethod
    def get_duplicate_group_by_hash(db: Session, sha256_hash: str) -> Optional[DuplicateGroup]:
        """Retrieve a duplicate group by exact SHA-256 hash."""
        return db.scalar(
            select(DuplicateGroup)
            .options(
                joinedload(DuplicateGroup.first_file),
                joinedload(DuplicateGroup.latest_file),
                joinedload(DuplicateGroup.members).joinedload(DuplicateMember.file),
            )
            .where(DuplicateGroup.sha256 == sha256_hash)
        )

    @staticmethod
    def get_duplicate_group_by_id(db: Session, group_id: int) -> Optional[DuplicateGroup]:
        """Retrieve a duplicate group by primary key ID."""
        return db.scalar(
            select(DuplicateGroup)
            .options(
                joinedload(DuplicateGroup.first_file),
                joinedload(DuplicateGroup.latest_file),
                joinedload(DuplicateGroup.members).joinedload(DuplicateMember.file),
            )
            .where(DuplicateGroup.id == group_id)
        )

    @staticmethod
    def get_duplicate_group_files(db: Session, group_id: int) -> List[File]:
        """Retrieve all currently present member files for a duplicate group."""
        stmt = (
            select(File)
            .join(DuplicateMember, DuplicateMember.file_id == File.id)
            .where(DuplicateMember.duplicate_group_id == group_id, File.is_present.is_(True))
            .order_by(File.first_seen_at.asc())
        )
        return list(db.scalars(stmt).all())
