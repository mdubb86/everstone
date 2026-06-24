"""es cal helpers — Google Calendar view/format + read-only policy.

No CLI: the agent reaches calendar ops via the es_cal_* MCP tools, which import
these helpers. Kept here (not inlined into mcp_server) so the cal view/bounds/
overlap logic stays unit-testable and in one place.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from es.capabilities import cal_support


def _localize(dt_str: str, tz: str) -> str:
    """RFC3339 dateTime -> ISO string in tz. Pass-through for all-day 'date'."""
    if "T" not in dt_str:           # all-day event ('date')
        return dt_str
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz)).isoformat()


def _event_view(e: dict, tz: str) -> dict:
    s = e.get("start", {})
    en = e.get("end", {})
    return {
        "id": e.get("id"),
        "summary": e.get("summary", ""),
        "start": _localize(s.get("dateTime") or s.get("date", ""), tz),
        "end": _localize(en.get("dateTime") or en.get("date", ""), tz),
        "location": e.get("location"),
    }


def _day_bounds(start: str, end: str, tz: str):
    """Accept YYYY-MM-DD (or full ISO); return RFC3339 timeMin/timeMax in tz."""
    z = ZoneInfo(tz)
    smin = datetime.fromisoformat(start) if "T" in start else datetime.fromisoformat(start + "T00:00:00")
    smax = datetime.fromisoformat(end) if "T" in end else datetime.fromisoformat(end + "T00:00:00")
    return smin.replace(tzinfo=z).isoformat(), smax.replace(tzinfo=z).isoformat()


def _instant(e: dict, key: str) -> str:
    """Comparable RFC3339 instant for ordering/overlap (UTC normalized)."""
    v = e.get(key, {})
    raw = v.get("dateTime") or (v.get("date", "") + "T00:00:00+00:00")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ZoneInfo("UTC")).isoformat()


class ReadOnlyCalendar(Exception):
    es_code = "read_only_calendar"


def _require_writable(calendar: str) -> None:
    read_only, _ = cal_support.calendar_policy()
    if calendar in read_only:
        raise ReadOnlyCalendar(
            f"{calendar!r} is read-only by policy; writes are refused. "
            f"Use a writable calendar instead."
        )
