"""es clock helper — the agent's only source of "now".

Why this exists: Hermes puts a DATE-ONLY line in the system prompt, labelled
`Conversation started: <date>`, deliberately coarse so the prompt stays
byte-stable for KV-cache reuse (agent/system_prompt.py). Its comment says
outright: "The model can still query the exact wall-clock time via tools when it
actually needs it."

EverStone locks the agent to the curated es_* tools with no terminal — so it
removed the tools that assumption relies on. A DM session created 2026-06-26 was
still reporting that date seven weeks later, and the agent had no way to check.
Everything time-relative ("this weekend", "this afternoon") was silently wrong,
and es_weather rejects past windows outright, so a stale date turns into a hard
tool failure rather than a wrong answer.

The zone comes from config `timezone`, NOT the container clock's zone: the dev
container ships with TZ unset (UTC) while prod runs America/Chicago, so trusting
the process zone would give a different answer in each.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from es.capabilities import cal_support


class CurrentTime(BaseModel):
    iso: str            # 2026-08-13T14:31:07-05:00
    date: str           # 2026-08-13
    time: str           # 14:31
    weekday: str        # Thursday
    timezone: str       # America/Chicago
    utc_offset: str     # -05:00
    utc: str            # 2026-08-13T19:31:07+00:00


def now(tz: str | None = None, _clock=None) -> CurrentTime:
    tzname = tz or cal_support.home_tz()
    zone = ZoneInfo(tzname)
    dt = (_clock or datetime.now)(zone)
    return CurrentTime(
        iso=dt.isoformat(timespec="seconds"),
        date=dt.strftime("%Y-%m-%d"),
        time=dt.strftime("%H:%M"),
        weekday=dt.strftime("%A"),
        timezone=tzname,
        utc_offset=dt.strftime("%z")[:3] + ":" + dt.strftime("%z")[3:],
        utc=dt.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"),
    )
