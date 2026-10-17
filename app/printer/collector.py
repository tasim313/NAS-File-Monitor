"""Brother MFC-L6900DW hardware metrics collector and NAS scan correlator.

Collects real-time device status, page counters, consumables (toner & drum),
hardware error history from http://192.168.4.171 and correlates scanned documents
deposited on the NAS requisition storage with printer scan activity records.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import httpx
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import PrinterDevice, PrinterLog
from app.datetime_util import utc_now
from app.logging_config import get_logger
from app.printer.ldap_service import ldap_service

logger = get_logger("app.printer.collector")


class PrinterCollector:
    """Manages Brother MFC-L6900DW communication, EWS scraping, and scan file correlation."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.host = self.settings.PRINTER_HOST
        self.password = self.settings.PRINTER_PASSWORD
        self.base_url = f"http://{self.host}"

    def _login(self, client: httpx.Client) -> bool:
        """Authenticate session against Brother Embedded Web Server (EWS)."""
        try:
            r = client.get(f"{self.base_url}/general/status.html", timeout=6.0)
            if "Logout" in r.text and "LogBox" not in r.text:
                return True

            csrf_match = re.search(r'name=\"CSRFToken\" value=\"([^\"]+)\"', r.text)
            if not csrf_match:
                return False
            token = csrf_match.group(1)

            post_data = {
                "CSRFToken": token,
                "B134e": self.password,
                "loginurl": "/general/status.html",
                "pageid": "1",
            }
            resp = client.post(f"{self.base_url}/general/status.html", data=post_data, timeout=6.0)
            return resp.status_code == 200 and ("logout" in resp.text.lower())
        except Exception as exc:
            logger.warning("Brother printer login failed (%s): %s", self.host, exc)
            return False

    def poll_device_status(self, db: Session) -> Optional[PrinterDevice]:
        """Poll Brother printer status, counters, toner and drum levels."""
        if not self.settings.PRINTER_ENABLED:
            return None

        device = db.query(PrinterDevice).filter(PrinterDevice.ip_address == self.host).first()
        if not device:
            device = PrinterDevice(
                ip_address=self.host,
                model_name="Brother MFC-L6900DW series",
                node_name="brw485f99ca71a8",
                status="Unknown",
                toner_level=80,
                drum_level=98,
            )
            db.add(device)
            db.flush()

        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                logged_in = self._login(client)

                # Fetch Status page
                status_res = client.get(f"{self.base_url}/general/status.html")
                status = "Ready"
                status_match = re.search(r'<span class=\"moni [^\"]*\">([^<]+)</span>', status_res.text)
                if status_match:
                    status = status_match.group(1).strip()

                # Toner level calculation
                toner_match = re.search(r'class=\"tonerremain\" height=\"(\d+)\"', status_res.text)
                toner_pct = round((int(toner_match.group(1)) / 55.0) * 100) if toner_match else device.toner_level

                # Fetch Maintenance Information page
                info_res = client.get(f"{self.base_url}/general/information.html?kind=item")
                def parse_item(label: str) -> Optional[str]:
                    m = re.search(rf'{label}<!-- mid-end -->[^\<]*</d[td]>\s*<dd>([^<]+)', info_res.text)
                    if not m:
                        m = re.search(rf'{label}[^<]*</d[td]>\s*<dd>([^<]+)', info_res.text)
                    return m.group(1).strip() if m else None

                serial = parse_item("Serial&#32;no.") or device.serial_number
                model = parse_item("Model&#32;Name") or device.model_name

                # Total Page count: look for Total Page Count section or Page Count
                total_pages_match = re.search(r'Total&#32;Page&#32;Count<!-- mid-end -->[\s\S]*?<dd>(\d+)', info_res.text)
                if not total_pages_match:
                    total_pages_match = re.search(r'<dt>[^<]*Page&#32;Count[^<]*</dt>\s*<dd>(\d+)', info_res.text)
                total_pages = int(total_pages_match.group(1)) if total_pages_match else device.total_pages

                # Printed pages
                print_match = re.search(r'<dt>[^<]*Print<!-- mid-end -->[^<]*</dt>\s*<dd>(\d+)', info_res.text)
                total_printed = int(print_match.group(1)) if print_match else device.total_printed

                # Total Scanned
                scan_match = re.search(r'Scan&#32;Page&#32;Count<!-- mid-end -->[^<]*</dt>\s*<dd>(\d+)', info_res.text)
                total_scanned = int(scan_match.group(1)) if scan_match else device.total_scanned

                # Copied pages
                copy_match = re.search(r'Copy<!-- mid-end -->[^<]*</dt>\s*<dd>(\d+)', info_res.text)
                total_copied = int(copy_match.group(1)) if copy_match else device.total_copied

                # Total Paper Jams
                jam_match = re.search(r'Total&#32;Paper&#32;Jams<!-- mid-end -->[^<]*</dt>\s*<dd>(\d+)', info_res.text)
                total_jams = int(jam_match.group(1)) if jam_match else device.total_jams

                # Drum Unit remaining percentage
                drum_match = re.search(r'Drum&#32;Unit<!-- mid-end -->[^<]*</dt>\s*<dd>(\d+)', info_res.text)
                drum_pct = int(drum_match.group(1)) if drum_match else device.drum_level

                # Parse Error History
                errors = []
                for row in re.finditer(r'<tr><th>(\d+)</th><td>([^<]+)</td><td>[^:]*:&nbsp;(\d+)</td></tr>', info_res.text):
                    errors.append({
                        "index": int(row.group(1)),
                        "error": row.group(2).strip(),
                        "page": int(row.group(3)),
                    })
                error_history_json = json.dumps(errors) if errors else device.error_history

                # Update device instance
                device.status = status
                device.toner_level = toner_pct
                device.drum_level = drum_pct
                device.model_name = model
                device.serial_number = serial
                device.total_pages = total_pages
                device.total_printed = total_printed
                device.total_scanned = total_scanned
                device.total_copied = total_copied
                device.total_jams = total_jams
                device.last_error = errors[0]["error"] if errors else device.last_error
                device.error_history = error_history_json
                device.last_polled_at = utc_now()
                db.commit()
                logger.info("Printer polled successfully: status=%s, toner=%d%%, pages=%d",
                            status, toner_pct, total_pages)
                return device
        except Exception as exc:
            db.rollback()
            logger.warning("Failed to poll Brother printer metrics: %s", exc)
            return device

    def ensure_syslog_configured(self) -> bool:
        """Verify Brother printer has Syslog enabled and configured to point to our host."""
        if not self.settings.PRINTER_SYSLOG_ENABLED:
            return False

        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                if not self._login(client):
                    return False

                syslog_page = client.get(f"{self.base_url}/net/net/syslog.html?pageid=411")
                token_match = re.search(r'name=\"CSRFToken\" value=\"([^\"]+)\"', syslog_page.text)
                if not token_match:
                    return False
                token = token_match.group(1)

                # Check if already enabled and pointing to 192.168.4.52:1514
                if 'value="1" checked="checked"' in syslog_page.text and '192.168.4.52' in syslog_page.text:
                    return True

                post_data = {
                    "pageid": "411",
                    "CSRFToken": token,
                    "B128b": "1",  # Enabled
                    "B12ad": "192.168.4.52",
                    "B12ae": str(self.settings.PRINTER_SYSLOG_PORT),
                    "B12b4": "1",  # UDP
                    "B12b1": "0",  # TLS Disabled
                    "B12af": "1",  # Local0
                    "B12b0": "7",  # Debug
                }
                resp = client.post(f"{self.base_url}/net/net/syslog.html", data=post_data)
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("Failed to configure Syslog on Brother printer: %s", exc)
            return False

    def correlate_nas_scans(self, db: Session, nas_path: Optional[Path] = None) -> int:
        """Scan NAS folder for scanned PDF files and record corresponding SCAN log events."""
        target_dir = nas_path or self.settings.NAS_DIRECTORY
        # Check base directory: if target_dir is /media/requisition/Report, also check parent /media/requisition
        dirs_to_check = [target_dir]
        if target_dir.parent.exists() and target_dir.parent != target_dir:
            dirs_to_check.append(target_dir.parent)

        ingested_count = 0
        for scan_dir in dirs_to_check:
            if not scan_dir.exists():
                continue

            try:
                for entry in os.scandir(scan_dir):
                    if not entry.is_file() or not entry.name.lower().endswith(".pdf"):
                        continue

                    # Check if log already recorded for this document
                    existing = db.query(PrinterLog).filter(PrinterLog.document_name == entry.name).first()
                    if existing:
                        continue

                    file_path = Path(entry.path)
                    stat = entry.stat()
                    file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

                    # Extract page count from PDF using pypdf
                    page_count = 1
                    try:
                        reader = PdfReader(str(file_path))
                        page_count = max(1, len(reader.pages))
                    except Exception:
                        pass

                    # Detect LDAP user: check scan profile name from folder or default to lab user
                    parent_name = scan_dir.name.lower()
                    inferred_uid = "tasim"  # Default known operator
                    if parent_name in ["worksheet", "report"]:
                        inferred_uid = "tasim"
                    elif parent_name.startswith("dr") or parent_name in ["arif", "roji", "ayman"]:
                        inferred_uid = parent_name

                    user_info = ldap_service.resolve_user(inferred_uid, db=db)

                    # Create SCAN log entry
                    log_entry = PrinterLog(
                        timestamp=file_mtime,
                        event_type="SCAN",
                        username=user_info["uid"],
                        user_display_name=user_info["cn"],
                        user_department=user_info["department"],
                        client_pc="MFC-L6900DW (Scan to Network)",
                        document_name=entry.name,
                        document_type="PDF",
                        page_count=page_count,
                        two_sided=False,
                        status="SUCCESS",
                        source="NAS_SCAN",
                        details=f"Stored in {scan_dir.name}; Size: {stat.st_size} bytes",
                    )
                    db.add(log_entry)
                    ingested_count += 1
            except Exception as exc:
                logger.warning("Error reading scan directory %s: %s", scan_dir, exc)

        if ingested_count > 0:
            db.commit()
            logger.info("Correlated and ingested %d scan logs from NAS files.", ingested_count)
        return ingested_count


# Global singleton instance
printer_collector = PrinterCollector()
