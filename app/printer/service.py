"""High-level service API for Brother printer, scanner, and OpenLDAP audit events."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.database.models import LdapUser, PrinterDevice, PrinterLog
from app.datetime_util import utc_now
from app.printer.collector import printer_collector
from app.printer.ldap_service import ldap_service
from app.websocket_manager import ws_manager


class PrinterService:
    """Provides querying and orchestration for printer hardware and audit logs."""

    @staticmethod
    def get_device_status(db: Session) -> Optional[Dict[str, Any]]:
        """Retrieve latest printer device status and counters."""
        device = db.query(PrinterDevice).order_by(PrinterDevice.id.asc()).first()
        if not device:
            # Poll once to initialize
            device = printer_collector.poll_device_status(db)
        return device.to_dict() if device else None

    @staticmethod
    def get_logs(
        db: Session,
        event_type: Optional[str] = None,
        username: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Query printer logs with filtering and pagination."""
        query = db.query(PrinterLog)

        if event_type and event_type.upper() != "ALL":
            query = query.filter(PrinterLog.event_type == event_type.upper())

        if username:
            query = query.filter(PrinterLog.username.ilike(f"%{username}%"))

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    PrinterLog.document_name.ilike(search_pattern),
                    PrinterLog.username.ilike(search_pattern),
                    PrinterLog.user_display_name.ilike(search_pattern),
                    PrinterLog.client_pc.ilike(search_pattern),
                )
            )

        total_count = query.count()
        logs = query.order_by(desc(PrinterLog.timestamp)).offset(offset).limit(limit).all()

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "items": [log.to_dict() for log in logs],
        }

    @staticmethod
    def get_ldap_users(
        db: Session,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Query cached LDAP users with activity metrics."""
        query = db.query(LdapUser)
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    LdapUser.uid.ilike(pattern),
                    LdapUser.cn.ilike(pattern),
                    LdapUser.department.ilike(pattern),
                    LdapUser.mail.ilike(pattern),
                )
            )

        total_count = query.count()
        users = query.order_by(desc(LdapUser.last_seen_at), LdapUser.cn.asc()).offset(offset).limit(limit).all()

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "items": [u.to_dict() for u in users],
        }

    @staticmethod
    def sync_all(db: Session) -> Dict[str, Any]:
        """Perform comprehensive sync: poll printer EWS, sync LDAP directory, correlate NAS scans."""
        device = printer_collector.poll_device_status(db)
        ldap_count = ldap_service.sync_users_from_ldap(db)
        scans_count = printer_collector.correlate_nas_scans(db)

        # Broadcast update
        if device:
            ws_manager.broadcast_sync({
                "type": "printer_status_update",
                "payload": device.to_dict(),
            })

        return {
            "status": "success",
            "device": device.to_dict() if device else None,
            "ldap_users_synced": ldap_count,
            "nas_scans_ingested": scans_count,
        }


printer_service = PrinterService()
