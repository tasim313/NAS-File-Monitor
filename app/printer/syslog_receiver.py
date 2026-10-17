"""Background UDP Syslog receiver for Brother MFC-L6900DW real-time audit logging.

Listens on UDP port 1514 for RFC 5424/3164 audit syslog packets emitted by the
Brother multifunction printer, extracts OpenLDAP authentication logins, print jobs,
scan events, and client IP addresses, and records them in the database.
"""

import asyncio
from datetime import datetime, timezone
import re
import socket
import threading
from typing import Any, Dict, Optional, Tuple

from app.config import get_settings
from app.database.database import SessionLocal
from app.database.models import LdapUser, PrinterLog
from app.datetime_util import utc_now
from app.logging_config import get_logger
from app.printer.ldap_service import ldap_service
from app.websocket_manager import ws_manager

logger = get_logger("app.printer.syslog")


class BrotherSyslogProtocol(asyncio.DatagramProtocol):
    """Asyncio UDP datagram protocol handling incoming printer syslog packets."""

    def __init__(self, receiver: "SyslogReceiver") -> None:
        self.receiver = receiver

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
        logger.info("Brother Syslog UDP listener ready on %s:%d",
                    self.receiver.host, self.receiver.port)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        text = data.decode("utf-8", errors="replace").strip()
        self.receiver.handle_message(text, addr[0])

    def error_received(self, exc: Exception) -> None:
        logger.warning("Brother Syslog socket error: %s", exc)


class SyslogReceiver:
    """Manages the UDP Syslog listener lifecycle and message parsing."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.host = self.settings.PRINTER_SYSLOG_HOST
        self.port = self.settings.PRINTER_SYSLOG_PORT
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._transport: Optional[asyncio.DatagramTransport] = None

    def start(self) -> None:
        """Start UDP listener in a dedicated background thread."""
        if not self.settings.PRINTER_SYSLOG_ENABLED or self.running:
            return

        self.running = True
        self._thread = threading.Thread(target=self._run_loop, name="BrotherSyslogThread", daemon=True)
        self._thread.start()
        logger.info("Started Brother Syslog receiver thread.")

    def _run_loop(self) -> None:
        """Run dedicated event loop for asynchronous UDP packet handling."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            listen = self._loop.create_datagram_endpoint(
                lambda: BrotherSyslogProtocol(self),
                local_addr=(self.host, self.port),
                family=socket.AF_INET,
            )
            transport, protocol = self._loop.run_until_complete(listen)
            self._transport = transport
            self._loop.run_forever()
        except Exception as exc:
            logger.error("Syslog server error on %s:%d: %s", self.host, self.port, exc)
        finally:
            self.running = False
            if self._loop and not self._loop.is_closed():
                self._loop.close()

    def stop(self) -> None:
        """Stop UDP listener and cleanup resources."""
        self.running = False
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Brother Syslog receiver stopped.")

    def parse_syslog_text(self, text: str, source_ip: str) -> Dict[str, Any]:
        """Parse RFC 5424 / Brother AuditLog structured text into attributes."""
        # Clean header: e.g. <135>1 2026-10-05T11:01:12+06:00 brw485f99ca71a8 Printer - AuditLog - ...
        timestamp = utc_now()
        ts_match = re.search(r'\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\b', text)
        if ts_match:
            try:
                from datetime import datetime
                timestamp = datetime.fromisoformat(ts_match.group(1))
            except Exception:
                pass

        # Extract key="value" or key=value pairs
        params = {}
        for match in re.finditer(r'([a-zA-Z_0-9]+)=("[^"]*"|\S+)', text):
            k = match.group(1).lower()
            v = match.group(2).strip('"')
            params[k] = v

        action = params.get("action", "").lower()
        event_type = "SYSTEM"
        if "login" in action or "auth" in action:
            event_type = "LOGIN"
        elif "logout" in action:
            event_type = "LOGOUT"
        elif "fail" in action or "denied" in action:
            event_type = "AUTH_FAIL"
        elif "print" in action:
            event_type = "PRINT"
        elif "scan" in action:
            event_type = "SCAN"
        elif "copy" in action:
            event_type = "COPY"
        elif "test" in action:
            event_type = "TEST"

        username = params.get("user") or params.get("username") or params.get("uid") or "admin"
        document_name = params.get("doc") or params.get("document") or params.get("file")
        client_pc = params.get("client") or params.get("pc") or params.get("host") or source_ip
        page_count = int(params.get("pages") or params.get("page_count") or 1)
        two_sided = params.get("duplex", "false").lower() in ["1", "true", "yes"]
        status = "SUCCESS"
        if params.get("result", "").lower() in ["failure", "failed", "error", "ng"]:
            status = "FAILED"

        return {
            "timestamp": timestamp,
            "event_type": event_type,
            "username": username,
            "document_name": document_name,
            "client_pc": client_pc,
            "page_count": page_count,
            "two_sided": two_sided,
            "status": status,
            "raw_text": text,
        }

    def handle_message(self, text: str, source_ip: str) -> None:
        """Process incoming syslog message, enrich with OpenLDAP metadata, and persist."""
        logger.debug("Received Syslog from %s: %s", source_ip, text)
        parsed = self.parse_syslog_text(text, source_ip)

        db = SessionLocal()
        try:
            # Resolve user details from OpenLDAP
            user_info = ldap_service.resolve_user(parsed["username"], db=db)

            # Record in PrinterLog
            log_entry = PrinterLog(
                timestamp=parsed["timestamp"],
                event_type=parsed["event_type"],
                username=user_info["uid"],
                user_display_name=user_info["cn"],
                user_department=user_info["department"],
                client_pc=parsed["client_pc"],
                document_name=parsed["document_name"],
                document_type="PDF" if parsed["document_name"] and parsed["document_name"].endswith(".pdf") else "DOCUMENT",
                page_count=parsed["page_count"],
                two_sided=parsed["two_sided"],
                status=parsed["status"],
                source="SYSLOG",
                details=parsed["raw_text"],
            )
            db.add(log_entry)

            # Update LdapUser counters and last seen
            ldap_user = db.query(LdapUser).filter(LdapUser.uid == user_info["uid"]).first()
            if ldap_user:
                ldap_user.last_seen_at = parsed["timestamp"]
                if parsed["event_type"] == "LOGIN":
                    ldap_user.login_count += 1
                elif parsed["event_type"] == "PRINT":
                    ldap_user.print_count += parsed["page_count"]
                elif parsed["event_type"] == "SCAN":
                    ldap_user.scan_count += parsed["page_count"]

            db.commit()
            db.refresh(log_entry)

            # Broadcast real-time event to connected WebSockets
            ws_manager.broadcast_sync({
                "type": "printer_log_event",
                "payload": log_entry.to_dict(),
            })
            logger.info("Saved printer audit event: type=%s, user=%s, doc=%s",
                        parsed["event_type"], user_info["uid"], parsed["document_name"])
        except Exception as exc:
            db.rollback()
            logger.error("Failed to process Brother syslog packet: %s", exc)
        finally:
            db.close()


# Global singleton instance
syslog_receiver = SyslogReceiver()
