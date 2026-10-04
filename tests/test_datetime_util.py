"""Tests for datetime utilities and Asia/Dhaka 12-hour formatting."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest

from app.datetime_util import DHAKA_TZ, format_dhaka_12h, format_dhaka_time_only, to_dhaka_datetime


def test_to_dhaka_datetime_from_utc_iso():
    # 09:38:33 UTC -> 15:38:33 Asia/Dhaka (+6 hrs) -> 03:38:33 PM
    iso_val = "2026-10-03T09:38:33.227247+00:00"
    dt = to_dhaka_datetime(iso_val)
    assert dt is not None
    assert dt.hour == 15
    assert dt.minute == 38
    assert dt.second == 33
    assert dt.tzinfo.key == "Asia/Dhaka"


def test_format_dhaka_12h_from_utc():
    # Verify the exact user scenario: 2026-10-03T09:38:33 -> 2026-10-03 03:38:33 PM
    iso_val = "2026-10-03T09:38:33.227247+00:00"
    formatted = format_dhaka_12h(iso_val)
    assert formatted == "2026-10-03 03:38:33 PM"


def test_format_dhaka_12h_naive_assumed_utc():
    # When datetime string has no timezone offset, assume UTC
    iso_val = "2026-10-03T09:38:33"
    formatted = format_dhaka_12h(iso_val)
    assert formatted == "2026-10-03 03:38:33 PM"


def test_format_dhaka_12h_morning_am():
    # 02:15:00 UTC -> 08:15:00 Asia/Dhaka -> 08:15:00 AM
    iso_val = "2026-10-03T02:15:00Z"
    formatted = format_dhaka_12h(iso_val)
    assert formatted == "2026-10-03 08:15:00 AM"


def test_format_dhaka_time_only():
    iso_val = "2026-10-03T09:38:33Z"
    assert format_dhaka_time_only(iso_val) == "03:38:33 PM"


def test_format_dhaka_empty_or_none():
    assert format_dhaka_12h(None) == "-"
    assert format_dhaka_12h("") == "-"
    assert format_dhaka_12h(None, default="N/A") == "N/A"


def test_models_to_dict_dhaka_formatting():
    from app.database.models import File, FileEvent, ScanRun
    now_utc = datetime(2026, 9, 30, 14, 7, 43, tzinfo=timezone.utc)
    f = File(
        file_name="HPL2609-00280.pdf",
        file_path="/media/requisition/Report/HPL2609-00280.pdf",
        relative_path="HPL2609-00280.pdf",
        file_size=120891,
        sha256="abc123def456",
        mtime=now_utc,
        first_seen_at=now_utc,
        last_seen_at=now_utc,
    )
    d = f.to_dict()
    assert d["mtime_dhaka"] == "2026-09-30 08:07:43 PM"
    assert d["first_seen_at_dhaka"] == "2026-09-30 08:07:43 PM"

    ev = FileEvent(
        event_type="ADDED",
        event_time=now_utc,
        file_path="/media/requisition/Report/HPL2609-00280.pdf",
        file_name="HPL2609-00280.pdf",
        sha256="abc123def456",
        file_size=120891,
    )
    ev.file = f
    ev_d = ev.to_dict()
    assert ev_d["event_time_dhaka"] == "2026-09-30 08:07:43 PM"
    assert ev_d["file_mtime_dhaka"] == "2026-09-30 08:07:43 PM"
