"""WebSocket Connection Manager and real-time live event broadcasting."""

import asyncio
from typing import Any, Dict, List, Optional
from fastapi import WebSocket
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.logging_config import get_logger

logger = get_logger("app.websocket")


class ConnectionManager:
    """Manages active browser WebSocket connections and broadcasts system updates."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def connect(self, websocket: WebSocket):
        """Accept new WebSocket connection and track it."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket client connected. Active connections: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        """Remove disconnected WebSocket from active pool."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket client disconnected. Active connections: %d", len(self.active_connections))

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast JSON message to all connected clients asynchronously."""
        if not self.active_connections:
            return

        dead_connections: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning("Error broadcasting to WebSocket client: %s", e)
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)

    def broadcast_sync(self, message: Dict[str, Any]):
        """Thread-safe synchronous broadcast method callable from background worker threads."""
        if not self.active_connections:
            return

        try:
            loop = self._loop or asyncio.get_event_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)
                return
        except RuntimeError:
            pass

        # Fallback: run in a dedicated event loop
        try:
            new_loop = asyncio.new_event_loop()
            new_loop.run_until_complete(self.broadcast(message))
            new_loop.close()
        except Exception as e:
            logger.error("Failed to broadcast sync event: %s", e)


# Global singleton instance
ws_manager = ConnectionManager()


def build_dashboard_payload(db: Session, scan_id: int | None = None) -> Dict[str, Any]:
    """Build current dashboard state payload for broadcasting or initial connection."""
    from app.database.models import DuplicateGroup, File
    from app.services.file_service import FileService
    from app.services.scan_service import ScanService

    total_present = db.scalar(
        select(func.count(File.id)).where(File.is_present.is_(True))
    ) or 0
    new_files_count = db.scalar(
        select(func.count(File.id)).where(File.status == "NEW", File.is_present.is_(True))
    ) or 0
    existing_files_count = db.scalar(
        select(func.count(File.id)).where(File.status == "EXISTING", File.is_present.is_(True))
    ) or 0
    removed_files_count = db.scalar(
        select(func.count(File.id)).where(File.is_present.is_(False))
    ) or 0
    duplicate_files_count = db.scalar(
        select(func.count(File.id)).where(File.is_duplicate.is_(True), File.is_present.is_(True))
    ) or 0
    duplicate_groups_count = db.scalar(
        select(func.count(DuplicateGroup.id)).where(DuplicateGroup.duplicate_count >= 2)
    ) or 0

    latest_scan = ScanService.get_latest_scan_run(db)
    recent_events = FileService.get_file_events(db, limit=10)

    return {
        "stats": {
            "total_files": total_present,
            "new_files": new_files_count,
            "existing_files": existing_files_count,
            "removed_files": removed_files_count,
            "duplicate_files": duplicate_files_count,
            "duplicate_groups": duplicate_groups_count,
        },
        "latest_scan": latest_scan.to_dict() if latest_scan else None,
        "recent_events": [e.to_dict() for e in recent_events],
    }


def handle_ws_api_command(db: Session, command: Dict[str, Any]) -> Dict[str, Any]:
    """Process an inbound WebSocket API query and return structured response payload."""
    from app.database.models import DuplicateGroup, DuplicateMember, File, FileEvent, ScanRun
    from app.services.duplicate_service import DuplicateService
    from app.services.file_service import FileService
    from app.services.scan_service import ScanService

    action = command.get("action", "")
    params = command.get("params") or {}

    query = params.get("query")
    status_filter = params.get("status")
    is_present = params.get("is_present")
    is_duplicate = params.get("is_duplicate")
    limit = int(params.get("limit", 50))
    offset = int(params.get("offset", 0))
    file_id = params.get("file_id")

    if action == "get_dashboard":
        return {
            "type": "DASHBOARD_DATA",
            "action": action,
            "status": "success",
            "data": build_dashboard_payload(db),
        }

    elif action == "get_files":
        stmt = select(File)
        if query:
            pat = f"%{query}%"
            stmt = stmt.where(or_(File.file_name.ilike(pat), File.file_path.ilike(pat), File.sha256.ilike(pat)))
        if status_filter:
            stmt = stmt.where(File.status == status_filter)
        if is_present is not None:
            stmt = stmt.where(File.is_present == is_present)
        if is_duplicate is not None:
            stmt = stmt.where(File.is_duplicate == is_duplicate)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0
        items = db.scalars(stmt.order_by(desc(File.last_seen_at)).limit(limit).offset(offset)).all()
        return {
            "type": "FILES_DATA",
            "action": action,
            "status": "success",
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [f.to_dict() for f in items],
            },
        }

    elif action == "get_new_files":
        stmt = select(File).where(File.status == "NEW", File.is_present.is_(True))
        if query:
            pat = f"%{query}%"
            stmt = stmt.where(or_(File.file_name.ilike(pat), File.file_path.ilike(pat)))
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0
        items = db.scalars(stmt.order_by(desc(File.first_seen_at)).limit(limit).offset(offset)).all()
        return {
            "type": "NEW_FILES_DATA",
            "action": action,
            "status": "success",
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [f.to_dict() for f in items],
            },
        }

    elif action == "get_previous_files":
        stmt = select(File).where(File.status == "EXISTING", File.is_present.is_(True))
        if query:
            pat = f"%{query}%"
            stmt = stmt.where(or_(File.file_name.ilike(pat), File.file_path.ilike(pat)))
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0
        items = db.scalars(stmt.order_by(desc(File.last_seen_at)).limit(limit).offset(offset)).all()
        return {
            "type": "PREVIOUS_FILES_DATA",
            "action": action,
            "status": "success",
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [f.to_dict() for f in items],
            },
        }

    elif action == "get_duplicates":
        stmt = (
            select(DuplicateGroup)
            .options(
                joinedload(DuplicateGroup.first_file),
                joinedload(DuplicateGroup.latest_file),
                joinedload(DuplicateGroup.members).joinedload(DuplicateMember.file),
            )
            .where(DuplicateGroup.duplicate_count >= 2)
        )
        if query:
            pat = f"%{query}%"
            stmt = stmt.where(DuplicateGroup.sha256.ilike(pat))
        count_stmt = select(func.count(DuplicateGroup.id)).where(DuplicateGroup.duplicate_count >= 2)
        total = db.scalar(count_stmt) or 0
        groups = db.scalars(
            stmt.order_by(desc(DuplicateGroup.latest_seen_at)).limit(limit).offset(offset)
        ).unique().all()
        results = []
        for g in groups:
            g_dict = g.to_dict()
            g_dict["first_file"] = g.first_file.to_dict() if g.first_file else None
            g_dict["latest_file"] = g.latest_file.to_dict() if g.latest_file else None
            g_dict["members"] = [
                {
                    "member_id": m.id,
                    "first_seen_at": m.first_seen_at.isoformat() if m.first_seen_at else None,
                    "last_seen_at": m.last_seen_at.isoformat() if m.last_seen_at else None,
                    "file": m.file.to_dict() if m.file else None,
                }
                for m in g.members
                if m.file and m.file.is_present
            ]
            results.append(g_dict)
        return {
            "type": "DUPLICATES_DATA",
            "action": action,
            "status": "success",
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "groups": results,
            },
        }

    elif action == "get_removed_files":
        stmt = select(File).where(File.is_present.is_(False))
        if query:
            pat = f"%{query}%"
            stmt = stmt.where(or_(File.file_name.ilike(pat), File.file_path.ilike(pat)))
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0
        items = db.scalars(stmt.order_by(desc(File.removed_at)).limit(limit).offset(offset)).all()
        return {
            "type": "REMOVED_FILES_DATA",
            "action": action,
            "status": "success",
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [f.to_dict() for f in items],
            },
        }

    elif action == "get_file_details":
        target_id = int(file_id) if file_id is not None else 1
        file_record = db.scalar(
            select(File)
            .options(
                joinedload(File.events),
                joinedload(File.duplicate_memberships).joinedload(DuplicateMember.duplicate_group),
            )
            .where(File.id == target_id)
        )
        if not file_record:
            return {
                "type": "FILE_DETAILS_DATA",
                "action": action,
                "status": "error",
                "data": {"detail": f"File with ID {target_id} not found."},
            }
        file_dict = file_record.to_dict()
        file_dict["events"] = [e.to_dict() for e in file_record.events]
        duplicate_groups = []
        for m in file_record.duplicate_memberships:
            if m.duplicate_group and m.duplicate_group.duplicate_count >= 2:
                duplicate_groups.append(m.duplicate_group.to_dict())
        file_dict["duplicate_groups"] = duplicate_groups
        return {
            "type": "FILE_DETAILS_DATA",
            "action": action,
            "status": "success",
            "data": file_dict,
        }

    elif action == "get_events":
        stmt = select(FileEvent)
        if file_id is not None:
            stmt = stmt.where(FileEvent.file_id == int(file_id))
        event_type = params.get("event_type")
        if event_type:
            stmt = stmt.where(FileEvent.event_type == event_type)
        if query:
            pat = f"%{query}%"
            stmt = stmt.where(or_(FileEvent.file_name.ilike(pat), FileEvent.file_path.ilike(pat)))
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0
        items = db.scalars(stmt.order_by(desc(FileEvent.event_time)).limit(limit).offset(offset)).all()
        return {
            "type": "EVENTS_DATA",
            "action": action,
            "status": "success",
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [e.to_dict() for e in items],
            },
        }

    elif action == "get_scans":
        total = db.scalar(select(func.count(ScanRun.id))) or 0
        runs = ScanService.get_scan_runs(db, limit=limit, offset=offset)
        return {
            "type": "SCANS_DATA",
            "action": action,
            "status": "success",
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [r.to_dict() for r in runs],
            },
        }

    elif action == "get_health":
        from app import __version__
        from app.config import get_settings
        from app.scanner.scheduler import get_scan_scheduler
        settings = get_settings()
        nas_exists = settings.NAS_DIRECTORY.exists() and settings.NAS_DIRECTORY.is_dir()
        scheduler = get_scan_scheduler()
        return {
            "type": "HEALTH_DATA",
            "action": action,
            "status": "success",
            "data": {
                "status": "healthy" if nas_exists else "degraded",
                "version": __version__,
                "nas_directory": str(settings.NAS_DIRECTORY),
                "nas_available": nas_exists,
                "database": True,
                "scanner_running": ScanService.is_scan_running(),
                "scheduler_running": scheduler.is_running,
            },
        }

    elif action == "export_files":
        files = db.scalars(select(File).order_by(File.file_path)).all()
        return {
            "type": "EXPORT_FILES_DATA",
            "action": action,
            "status": "success",
            "data": [f.to_dict() for f in files],
        }

    elif action == "export_duplicates":
        groups = db.scalars(
            select(DuplicateGroup)
            .options(
                joinedload(DuplicateGroup.first_file),
                joinedload(DuplicateGroup.latest_file),
                joinedload(DuplicateGroup.members).joinedload(DuplicateMember.file),
            )
            .where(DuplicateGroup.duplicate_count >= 2)
            .order_by(desc(DuplicateGroup.duplicate_count))
        ).unique().all()
        results = []
        for g in groups:
            g_dict = g.to_dict()
            g_dict["first_file"] = g.first_file.to_dict() if g.first_file else None
            g_dict["latest_file"] = g.latest_file.to_dict() if g.latest_file else None
            g_dict["members"] = [
                m.file.to_dict() for m in g.members if m.file and m.file.is_present
            ]
            results.append(g_dict)
        return {
            "type": "EXPORT_DUPLICATES_DATA",
            "action": action,
            "status": "success",
            "data": results,
        }

    elif action == "trigger_scan":
        from app.scanner.scheduler import get_scan_scheduler
        from app.services.scan_service import ScanInProgressError
        scheduler = get_scan_scheduler()
        try:
            summary = scheduler.trigger_manual_scan()
            return {
                "type": "SCAN_TRIGGERED_DATA",
                "action": action,
                "status": "success",
                "data": summary,
            }
        except ScanInProgressError:
            return {
                "type": "SCAN_TRIGGERED_DATA",
                "action": action,
                "status": "error",
                "data": {"detail": "A scan is already in progress."},
            }
        except Exception as e:
            return {
                "type": "SCAN_TRIGGERED_DATA",
                "action": action,
                "status": "error",
                "data": {"detail": str(e)},
            }

    else:
        return {
            "type": "UNKNOWN_ACTION",
            "action": action,
            "status": "error",
            "data": {"detail": f"Unknown action: {action}"},
        }
