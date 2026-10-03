"""Date and time utilities with Asia/Dhaka timezone support."""

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

DHAKA_TZ = ZoneInfo("Asia/Dhaka")


def to_dhaka_datetime(val: Any) -> Optional[datetime]:
    """Convert input (string, datetime, timestamp) to timezone-aware Asia/Dhaka datetime."""
    if val is None or val == "":
        return None

    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc).astimezone(DHAKA_TZ)

    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return None
        if val_str.endswith("Z"):
            val_str = val_str[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(val_str)
        except Exception:
            try:
                dt = datetime.strptime(val_str[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
    elif isinstance(val, datetime):
        dt = val
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(DHAKA_TZ)


def format_dhaka_12h(val: Any, default: str = "-") -> str:
    """Format a datetime or ISO string to Asia/Dhaka 12-hour format: YYYY-MM-DD hh:mm:ss AM/PM.
    
    Example: 2026-10-03 03:38:33 PM
    """
    dt = to_dhaka_datetime(val)
    if dt is None:
        return default
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")


def format_dhaka_time_only(val: Any, default: str = "-") -> str:
    """Format to 12-hour time only: hh:mm:ss AM/PM."""
    dt = to_dhaka_datetime(val)
    if dt is None:
        return default
    return dt.strftime("%I:%M:%S %p")
