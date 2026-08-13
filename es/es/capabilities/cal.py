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


def _event_local(dt_str: str) -> str:
    """The event's OWN local time, offset intact. Only normalises 'Z' to
    '+00:00' so every value is uniformly parseable."""
    if "T" not in dt_str:           # all-day event ('date')
        return dt_str
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).isoformat()


def _offset_differs(dt_str: str, tz: str) -> bool:
    if "T" not in dt_str:
        return False
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.utcoffset() != dt.astimezone(ZoneInfo(tz)).utcoffset()


def _event_view(e: dict, tz: str) -> dict:
    """Report the event in its OWN timezone, adding home time only when they
    differ.

    Previously every event was collapsed into a single zone, so a 3pm meeting in
    San Francisco was reported to a Chicago operator as "5pm" — correct as an
    instant, useless if you are standing in San Francisco. Which zone is right
    depends on where the operator is, which `es` cannot know; reporting the
    event's own time sidesteps that entirely, because 3pm Pacific is true
    wherever you are.

    THE EVENT'S ZONE IS `start.timeZone`, NOT the offset in `dateTime`. Verified
    against the live API: events().list() RENDERS every event in the zone you ask
    for, defaulting to the calendar's, so the same event returns -05:00 with no
    timeZone param and -07:00 with timeZone=America/Los_Angeles. On a
    Chicago-default calendar every event therefore arrives looking like Chicago,
    and trusting that offset makes this function a no-op.

    Callers wanting a single-zone agenda still pass `tz` explicitly.
    """
    s = e.get("start", {})
    en = e.get("end", {})
    s_raw = s.get("dateTime") or s.get("date", "")
    e_raw = en.get("dateTime") or en.get("date", "")
    ev_tz = s.get("timeZone")
    out = {
        "id": e.get("id"),
        "summary": e.get("summary", ""),
        "start": _event_local(s_raw),
        "end": _event_local(e_raw),
        "location": e.get("location"),
    }
    if "T" not in s_raw or not ev_tz:
        # All-day, or no zone recorded on the event: the rendering zone is the
        # only thing we have, so leave it as-is rather than inventing one.
        return out
    out["start"] = _localize(s_raw, ev_tz)
    out["end"] = _localize(e_raw, ev_tz)
    out["tz"] = ev_tz
    if _offset_differs(out["start"], tz):
        out["start_home"] = _localize(s_raw, tz)
        out["end_home"] = _localize(e_raw, tz)
    return out


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
