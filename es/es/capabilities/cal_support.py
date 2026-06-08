"""Config + calendar-resolution helpers for es cal."""
from typing import List, Set, Tuple

from es import config

DEFAULT_TZ = "America/Chicago"


def calendar_policy() -> Tuple[Set[str], List[str]]:
    """Return (read_only set, read_write list) of calendar summaries from config."""
    cals = ((config.load_config().get("gcalcli") or {}).get("calendars") or {})
    read_only = set(cals.get("read_only") or [])
    read_write = list(cals.get("read_write") or [])
    return read_only, read_write


def home_tz() -> str:
    """Operator home timezone; Plan 3 adds the schema field. Falls back Central."""
    return config.load_config().get("timezone") or DEFAULT_TZ


def resolve_calendar_id(service, summary: str) -> str:
    """Map a calendar display name (summary) to its API id via calendarList."""
    items = service.calendarList().list().execute().get("items", [])
    for c in items:
        if c.get("summary") == summary:
            return c["id"]
    raise KeyError(f"calendar not found: {summary!r}")
