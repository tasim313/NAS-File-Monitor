"""WebSocket Connection Manager and real-time live event broadcasting."""

import asyncio
from typing import Any, Dict, List
from fastapi import WebSocket
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
