"""OpenLDAP integration service for Brother MFC-L6900DW authentication tracking.

Queries OpenLDAP directory at 192.168.1.19:389 (dc=aikhanlab,dc=com) to resolve
usernames (uid) to full names (cn), departments, email, and employee details.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import ldap3
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import LdapUser
from app.datetime_util import utc_now
from app.logging_config import get_logger

logger = get_logger("app.printer.ldap")


class LdapService:
    """Manages OpenLDAP connections, user queries, and database synchronization."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._user_cache: Dict[str, Dict[str, Any]] = {}

    def get_connection(self) -> Optional[ldap3.Connection]:
        """Establish an anonymous or simple bind connection to OpenLDAP."""
        if not self.settings.LDAP_ENABLED:
            return None
        try:
            server = ldap3.Server(
                self.settings.LDAP_SERVER,
                port=self.settings.LDAP_PORT,
                connect_timeout=3,
                get_info=ldap3.NONE,
            )
            conn = ldap3.Connection(server, auto_bind=True)
            return conn
        except Exception as exc:
            logger.warning("Could not connect to OpenLDAP (%s:%d): %s",
                           self.settings.LDAP_SERVER, self.settings.LDAP_PORT, exc)
            return None

    def sync_users_from_ldap(self, db: Session) -> int:
        """Fetch all person entries from OpenLDAP and upsert into database."""
        conn = self.get_connection()
        if not conn:
            logger.info("OpenLDAP offline or disabled; skipping sync.")
            return 0

        try:
            conn.search(
                search_base=self.settings.LDAP_BASE_DN,
                search_filter="(objectClass=person)",
                search_scope=ldap3.SUBTREE,
                attributes=["uid", "cn", "mail", "ou", "title", "mobile", "employeeNumber"],
            )

            count = 0
            for entry in conn.entries:
                uid_vals = entry.uid.values if hasattr(entry, "uid") else []
                if not uid_vals:
                    continue
                uid = str(uid_vals[0]).strip().lower()
                cn = str(entry.cn.value).strip() if hasattr(entry, "cn") and entry.cn.value else uid

                # Department from ou or DN parsing
                dept = None
                if hasattr(entry, "ou") and entry.ou.values:
                    dept = str(entry.ou.values[0])
                else:
                    # Parse dn: e.g. cn=...,ou=IT,ou=Department,dc=aikhanlab,dc=com
                    dn_parts = [p.strip() for p in entry.entry_dn.split(",")]
                    for p in dn_parts:
                        if p.lower().startswith("ou=") and not p.lower().startswith("ou=department"):
                            dept = p[3:]
                            break

                mail = str(entry.mail.value).strip() if hasattr(entry, "mail") and entry.mail.value else None
                title = str(entry.title.value).strip() if hasattr(entry, "title") and entry.title.value else None
                mobile = str(entry.mobile.value).strip() if hasattr(entry, "mobile") and entry.mobile.value else None
                emp_num = str(entry.employeeNumber.value).strip() if hasattr(entry, "employeeNumber") and entry.employeeNumber.value else None

                # Update in-memory cache
                self._user_cache[uid] = {
                    "uid": uid,
                    "cn": cn,
                    "department": dept,
                    "mail": mail,
                    "title": title,
                }

                # Upsert into database
                user_obj = db.query(LdapUser).filter(LdapUser.uid == uid).first()
                if not user_obj:
                    user_obj = LdapUser(
                        uid=uid,
                        cn=cn,
                        mail=mail,
                        department=dept,
                        title=title,
                        mobile=mobile,
                        employee_number=emp_num,
                    )
                    db.add(user_obj)
                else:
                    user_obj.cn = cn
                    user_obj.mail = mail or user_obj.mail
                    user_obj.department = dept or user_obj.department
                    user_obj.title = title or user_obj.title
                    user_obj.mobile = mobile or user_obj.mobile
                    user_obj.employee_number = emp_num or user_obj.employee_number
                count += 1

            db.commit()
            logger.info("Synchronized %d LDAP users successfully.", count)
            return count
        except Exception as exc:
            db.rollback()
            logger.error("Failed to sync LDAP users: %s", exc)
            return 0
        finally:
            try:
                conn.unbind()
            except Exception:
                pass

    def resolve_user(self, uid: Optional[str], db: Optional[Session] = None) -> Dict[str, Any]:
        """Resolve a username/uid to full details (cn, department, email)."""
        if not uid:
            return {"uid": "unknown", "cn": "Unknown User", "department": "-", "mail": None}

        clean_uid = uid.strip().lower()
        # Strip domain if format domain\user or user@domain
        if "\\" in clean_uid:
            clean_uid = clean_uid.split("\\")[-1]
        if "@" in clean_uid:
            clean_uid = clean_uid.split("@")[0]

        # Check memory cache first
        if clean_uid in self._user_cache:
            return self._user_cache[clean_uid]

        # Check database
        if db:
            user_obj = db.query(LdapUser).filter(LdapUser.uid == clean_uid).first()
            if user_obj:
                info = {
                    "uid": user_obj.uid,
                    "cn": user_obj.cn,
                    "department": user_obj.department or "-",
                    "mail": user_obj.mail,
                    "title": user_obj.title,
                }
                self._user_cache[clean_uid] = info
                return info

        # Direct LDAP lookup if not found in db
        conn = self.get_connection()
        if conn:
            try:
                conn.search(
                    search_base=self.settings.LDAP_BASE_DN,
                    search_filter=f"(uid={clean_uid})",
                    search_scope=ldap3.SUBTREE,
                    attributes=["uid", "cn", "mail", "ou", "title"],
                )
                if conn.entries:
                    e = conn.entries[0]
                    cn = str(e.cn.value).strip() if hasattr(e, "cn") and e.cn.value else clean_uid
                    dept = str(e.ou.values[0]) if hasattr(e, "ou") and e.ou.values else "-"
                    mail = str(e.mail.value).strip() if hasattr(e, "mail") and e.mail.value else None
                    info = {"uid": clean_uid, "cn": cn, "department": dept, "mail": mail}
                    self._user_cache[clean_uid] = info
                    return info
            except Exception:
                pass
            finally:
                try:
                    conn.unbind()
                except Exception:
                    pass

        # Fallback to formatted title
        fallback_cn = clean_uid.replace(".", " ").replace("_", " ").title()
        return {"uid": clean_uid, "cn": fallback_cn, "department": "-", "mail": None}


# Global singleton instance
ldap_service = LdapService()
