"""es cal — Google Calendar via the API directly (no gcalcli)."""
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

import typer

from es.google_auth import calendar_service
from es.runner import envelope
from es.capabilities import cal_support

app = typer.Typer(no_args_is_help=True)

GROUP_SAFE = False
CONFIG_KEYS = ("gcalcli.calendars", "timezone")


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


@app.command("agenda")
@envelope
def agenda(ctx: typer.Context,
           start: str = typer.Argument(...),
           end: str = typer.Argument(...),
           calendar: str = typer.Option(..., "--calendar"),
           tz: Optional[str] = typer.Option(None, "--tz")):
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    tmin, tmax = _day_bounds(start, end, tzname)
    items = svc.events().list(
        calendarId=cal_id, timeMin=tmin, timeMax=tmax,
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    return [_event_view(e, tzname) for e in items]


@app.command("search")
@envelope
def search(ctx: typer.Context,
           query: str = typer.Argument(...),
           calendar: str = typer.Option(..., "--calendar"),
           tz: Optional[str] = typer.Option(None, "--tz")):
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    items = svc.events().list(
        calendarId=cal_id, q=query, singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    return [_event_view(e, tzname) for e in items]


def _instant(e: dict, key: str) -> str:
    """Comparable RFC3339 instant for ordering/overlap (UTC normalized)."""
    v = e.get(key, {})
    raw = v.get("dateTime") or (v.get("date", "") + "T00:00:00+00:00")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ZoneInfo("UTC")).isoformat()


@app.command("conflicts")
@envelope
def conflicts(ctx: typer.Context,
              start: str = typer.Argument(...),
              end: str = typer.Argument(...),
              calendar: str = typer.Option(..., "--calendar"),
              tz: Optional[str] = typer.Option(None, "--tz")):
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    tmin, tmax = _day_bounds(start, end, tzname)
    items = svc.events().list(
        calendarId=cal_id, timeMin=tmin, timeMax=tmax,
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    # chronological sweep (ref: gcalcli/conflicts.py): a pair conflicts when the
    # later event starts before the earlier one ends.
    out: List[dict] = []
    active: List[dict] = []
    for e in items:
        s = _instant(e, "start")
        active = [a for a in active if _instant(a, "end") > s]
        for a in active:
            out.append({"a": _event_view(a, tzname), "b": _event_view(e, tzname)})
        active.append(e)
    return out
