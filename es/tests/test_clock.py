"""es_time tests.

Context: Hermes puts only `Conversation started: <date>` in the system prompt,
coarse by design for KV-cache stability, on the stated assumption that the model
can query the real time via a tool. EverStone locked the agent to es_* tools and
shipped no such tool — so a DM session created 2026-06-26 still believed that
date seven weeks later. These tests pin the contract that replaced that gap.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from es.capabilities import clock


def _fixed(dt):
    """A clock stuck at `dt`, so assertions don't drift with wall time."""
    return lambda zone: dt.astimezone(zone)


AUG = datetime(2026, 8, 13, 19, 31, 7, tzinfo=timezone.utc)   # 14:31 CDT
JAN = datetime(2026, 1, 15, 19, 31, 7, tzinfo=timezone.utc)   # 13:31 CST


def test_reports_local_date_weekday_and_offset():
    t = clock.now("America/Chicago", _clock=_fixed(AUG))
    assert t.date == "2026-08-13"
    assert t.weekday == "Thursday"
    assert t.time == "14:31"
    assert t.utc_offset == "-05:00"
    assert t.timezone == "America/Chicago"


def test_utc_is_reported_alongside_local():
    t = clock.now("America/Chicago", _clock=_fixed(AUG))
    assert t.utc.startswith("2026-08-13T19:31:07")
    assert t.iso.startswith("2026-08-13T14:31:07-05:00")


def test_offset_follows_dst():
    """CST in January, CDT in August — the offset is computed, not assumed."""
    assert clock.now("America/Chicago", _clock=_fixed(JAN)).utc_offset == "-06:00"
    assert clock.now("America/Chicago", _clock=_fixed(AUG)).utc_offset == "-05:00"


def test_explicit_timezone_gives_that_locations_local_time():
    t = clock.now("America/Los_Angeles", _clock=_fixed(AUG))
    assert t.time == "12:31" and t.utc_offset == "-07:00"


def test_a_no_dst_zone_is_handled():
    t = clock.now("America/Phoenix", _clock=_fixed(AUG))
    assert t.utc_offset == "-07:00"          # Phoenix never observes DST


def test_zone_comes_from_config_not_the_process_clock(monkeypatch):
    """The dev container ships with TZ unset (UTC) while prod runs
    America/Chicago. Trusting the process zone would give a different answer in
    each, so the zone must come from config."""
    monkeypatch.setattr(clock.cal_support, "home_tz", lambda: "America/New_York")
    assert clock.now(_clock=_fixed(AUG)).timezone == "America/New_York"
    assert clock.now(_clock=_fixed(AUG)).time == "15:31"


def test_date_and_weekday_agree():
    """Guards the pairing the agent reasons from: a weekday that disagrees with
    the date is worse than either alone."""
    t = clock.now("America/Chicago", _clock=_fixed(AUG))
    assert datetime.fromisoformat(t.iso).strftime("%A") == t.weekday
    assert datetime.fromisoformat(t.iso).strftime("%Y-%m-%d") == t.date
