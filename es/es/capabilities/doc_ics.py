"""iCalendar (.ics) feeds -> Markdown, one `##` heading per VEVENT.

Why per-event headings, not per-day grouping: the reader this feature exists
for (a later task) pages Markdown BY `##` heading. A real feed from a club
scheduling tool (PlayMetrics, TeamSnap, ...) is commonly 100+ VEVENTs with no
structure at all — rendered as prose that would be one giant section, which
defeats paging entirely (see docs.py's CONVERTERS docstring / the design note
this module was written against). Grouping by day ("## Sat Sep 5" with
several games listed under it) was considered and rejected: it reads a little
more naturally for a human skimming top-to-bottom, but it reintroduces the
same problem at a smaller scale (a tournament Saturday can hold 4-6 games
under one heading) AND makes the reader's per-page granularity depend on how
many events happen to land on the same calendar day — an accident of the
schedule, not a property the converter controls. One event per heading keeps
every page the same predictable shape (one event, always) regardless of how
the games happen to cluster.

Timezone handling (the subtle part — read before changing):
- DTSTART;VALUE=DATE (all-day) is a plain `datetime.date` with no time
  component. Rendered as a bare date; never given a fabricated "00:00".
- A naive DTSTART (no trailing 'Z', no TZID) parses as a tz-naive datetime.
  RFC 5545 calls this a "floating" time with no fixed zone — there is no
  correct zone to convert it TO, so it is displayed exactly as written rather
  than inventing one.
- A tz-aware DTSTART (the common case for real feeds: PlayMetrics/Google
  export UTC with a trailing 'Z') is converted to the operator's configured
  home zone via `cal_support.home_tz()` — the same convention es_cal/es_time
  already use (config `timezone`, default "America/Chicago"). A raw UTC
  timestamp read out of a converted Markdown document, with no per-line
  timezone annotation, is easy to misread as already-local; for the
  motivating case (a 9am Saturday game emitted as DTSTART 14:00Z) that
  misreading is actively wrong, not just imprecise, so it is resolved once
  here rather than left for whoever reads the Markdown to get wrong.
  `es` cannot discover a *per-feed* zone (unlike Google Calendar, an .ics
  file has no reliable per-calendar zone field it's safe to trust), so the
  single operator-configured zone is the only zone available to convert to.

Every heading includes the year, not just month/day. This looks redundant
when skimming the whole document top-to-bottom, but the reader this module
targets pages BY HEADING — the agent may see one `##` section with none of
its neighbors in context. A club season commonly spans a year boundary
(Sep 2026 - Jun 2027); a bare "Sun Jan 10, 8:00 AM" heading paged in
isolation gives the agent nothing to infer the year from, and it will
guess (usually the current year, which is wrong for half the season). Only
putting the year in the summary line (once, at the top) would read cleaner
but breaks the moment a section is read alone, so every heading pays the
few extra characters instead.

A multi-day event (e.g. a weekend tournament) shows its end date, not just
its end time, when that end date differs from the start date — "Ends 6:00
PM" on a Saturday-start event that actually runs through Monday is a wrong
answer, not an imprecise one.

RRULE (recurrence) is deliberately NOT expanded into repeated occurrences.
A `FREQ=WEEKLY;COUNT=30` practice stays exactly one heading/one event, for
two reasons: (1) expansion risks blowing well past MAX_ICS_CHARS on the
club/school feeds this module targets, where recurring weekly practices
sit alongside a full season of one-off games; (2) some real-world RRULEs
have neither COUNT nor UNTIL (open-ended recurrence) and are not safe to
expand into a finite list at all. What must not happen is *silently*
collapsing a season-long recurrence into what reads like a single
one-off event: the event's own block says so in-band, e.g.
"(repeats weekly, 30 times)" / "(repeats every 2 weeks, until Dec 20,
2026)" / "(repeats monthly, no end date)", and the feed summary line
counts how many events are recurring, e.g. "12 events (3 recurring)".
"""
from collections import Counter
from datetime import datetime, time
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from icalendar import Calendar

from es.capabilities import cal_support

# Character budget for the rendered feed, enforced by truncating at a whole
# EVENT boundary (never mid-event). Mirrors doc_text.MAX_CHARS's
# reasoning and its distance under docs.MAX_MARKDOWN_CHARS (40_000): this
# module truncates itself (docs._truncate_markdown only knows "## Page N"
# PDF-style boundaries, not per-event ones), and 30_000 leaves enough margin
# that docs.py's own outer truncation never has to fire a second time. A
# realistic 117-event feed (the PlayMetrics case this module exists for)
# comes in well under this — see the module's own manual size check in the
# task report, not asserted here since a real feed isn't a unit-test fixture.
MAX_ICS_CHARS = 30_000


def _home_tz() -> str:
    """cal_support.home_tz() already falls back to DEFAULT_TZ when config.yaml
    is present but has no `timezone` key; this extends the same fallback to
    the case where config.yaml is missing/unreadable entirely (e.g. no
    /opt/config.yaml mounted at all) — config.load_config() raises
    FileNotFoundError for that, uncaught by cal_support itself. A document
    converter must never hard-fail a conversion over a config lookup when a
    reasonable default (the same DEFAULT_TZ cal_support already uses) is
    right there; this only changes WHEN the fallback applies, not what it
    falls back to."""
    try:
        return cal_support.home_tz()
    except FileNotFoundError:
        return cal_support.DEFAULT_TZ


def _read_calendar(source: Path) -> Optional[Calendar]:
    """None means "could not be parsed as an .ics feed at all" — a malformed
    file must never raise out of convert(); real feeds are exactly the kind
    of hand-exported/third-party data most likely to be slightly broken."""
    try:
        return Calendar.from_ical(source.read_bytes())
    except Exception:
        return None


def _to_display(value, tz_name: str):
    """Normalize one DTSTART/DTEND value for display (see module docstring
    for the reasoning per case). Returns a date, a naive datetime, or a
    datetime localized to tz_name — never None (callers check for None
    themselves before calling this)."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo(tz_name))
        return value
    return value  # a plain date (all-day)


def _sort_key(value):
    """A single comparable key across the three shapes _to_display can
    return (date / naive datetime / tz-aware-but-already-localized datetime),
    so a feed mixing all-day and timed events still sorts consistently.
    Events with no start at all sort last, not first — an undated event is
    not "the earliest event", it's simply unordered."""
    if value is None:
        return datetime.max
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.combine(value, time.min)


def _format_when(value) -> str:
    """Always includes the year — see the module docstring: headings are
    paged in isolation, so each one must stand alone without relying on a
    neighboring section (or the current year) to disambiguate."""
    if value is None:
        return "(no date)"
    if isinstance(value, datetime):
        return value.strftime("%a %b %-d, %Y, %-I:%M %p")
    return value.strftime("%a %b %-d, %Y")


def _format_end(end, start) -> Optional[str]:
    """Time-only ("Ends 6:00 PM") unless the end falls on a different
    calendar date than the start (both already localized to the same
    tz_name by the caller) — a multi-day event must say so, or it reads as
    ending the same evening it started (see module docstring, item 3)."""
    if not isinstance(end, datetime):
        return None
    if isinstance(start, datetime) and end.date() != start.date():
        return end.strftime("%a %b %-d, %Y, %-I:%M %p")
    return end.strftime("%-I:%M %p")


_RRULE_UNIT = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}
_RRULE_ADVERB = {"DAILY": "daily", "WEEKLY": "weekly", "MONTHLY": "monthly", "YEARLY": "yearly"}


def _format_rrule(rrule, tz_name: str) -> Optional[str]:
    """Human-readable recurrence note, e.g. "(repeats weekly, 30 times)".
    RRULE is never expanded into individual occurrences — see module
    docstring, item on RRULE — so this is the only trace of recurrence
    that survives conversion; it must never be silently dropped."""
    if not rrule:
        return None
    freq = (rrule.get("FREQ") or [None])[0]
    if not freq:
        return None
    interval = (rrule.get("INTERVAL") or [1])[0]
    if interval and int(interval) > 1:
        unit = _RRULE_UNIT.get(freq, str(freq).lower())
        freq_desc = f"every {int(interval)} {unit}s"
    else:
        freq_desc = _RRULE_ADVERB.get(freq, f"every {_RRULE_UNIT.get(freq, str(freq).lower())}")

    count = (rrule.get("COUNT") or [None])[0]
    if count:
        return f"(repeats {freq_desc}, {int(count)} times)"

    until = (rrule.get("UNTIL") or [None])[0]
    if until is not None:
        until_display = _to_display(until, tz_name) if isinstance(until, datetime) else until
        return f"(repeats {freq_desc}, until {until_display.strftime('%b %-d, %Y')})"

    return f"(repeats {freq_desc}, no end date)"


def _event_block(ev, tz_name: str) -> str:
    summary = str(ev.get("summary") or "(no summary)")
    dtstart_prop = ev.get("dtstart")
    start = _to_display(dtstart_prop.dt, tz_name) if dtstart_prop is not None else None
    lines = [f"## {_format_when(start)} — {summary}"]

    rrule_note = _format_rrule(ev.get("rrule"), tz_name)
    if rrule_note:
        lines.append(rrule_note)

    location = ev.get("location")
    if location:
        lines.append(str(location))

    dtend_prop = ev.get("dtend")
    if dtend_prop is not None:
        end = _to_display(dtend_prop.dt, tz_name)
        end_str = _format_end(end, start)
        if end_str:
            lines.append(f"Ends {end_str}")

    description = ev.get("description")
    if description:
        lines.append(str(description))

    return "\n".join(lines)


def _build_markdown(events, calname: Optional[str], tz_name: str,
                     skipped: Optional[Counter] = None) -> str:
    total = len(events)
    count_bit = f"{total} event{'s' if total != 1 else ''}"

    recurring = sum(1 for _, ev in events if ev.get("rrule"))
    if recurring:
        count_bit += f" ({recurring} recurring)"

    if skipped:
        skipped_bits = ", ".join(
            f"{n} {name}{'s' if n != 1 else ''}"
            for name, n in sorted(skipped.items()))
        count_bit += (f" — also found {skipped_bits}, not shown (only "
                      "calendar events/VEVENT are converted)")

    header = f"{calname} — {count_bit}" if calname else count_bit

    lines = [header]
    used = len(header)
    kept = 0
    for _, ev in events:
        block = _event_block(ev, tz_name)
        cost = len(block) + 2  # blank line separating it from the previous block
        if kept > 0 and used + cost > MAX_ICS_CHARS:
            break
        lines.append("")
        lines.append(block)
        used += cost
        kept += 1

    md = "\n".join(lines)
    if kept < total:
        remaining = total - kept
        md += (f"\n\n*(truncated after {kept} of {total} events — the "
               f"{MAX_ICS_CHARS}-character limit was reached; a calendar "
               "feed has no page range to resume from, so ask for a "
               f"narrower date range or a smaller export if the remaining "
               f"{remaining} event{'s' if remaining != 1 else ''} are needed)*")
    return md


def convert(source: Path, adir: Path,
            pages: Optional[List[int]] = None, **_ignored) -> Tuple[str, List[Path]]:
    """Return (markdown, []) — a calendar feed produces no images.

    `pages` is accepted (matching every other converter's signature, which
    docs.extract calls uniformly) but unused: this module implements neither
    `page_count` nor `render`, so docs.py never lets an explicit `pages`
    argument reach here (see docs.py's _page_count / extract()).
    """
    cal = _read_calendar(source)
    if cal is None:
        return (
            "*(this file could not be read as an iCalendar (.ics) feed — it "
            "may be corrupt or not actually an .ics file; ask the user to "
            "resend it)*"
        ), []

    tz_name = _home_tz()
    events = []
    for ev in cal.walk("VEVENT"):
        dtstart_prop = ev.get("dtstart")
        start = _to_display(dtstart_prop.dt, tz_name) if dtstart_prop is not None else None
        events.append((_sort_key(start), ev))
    events.sort(key=lambda pair: pair[0])

    # Non-VEVENT content (VTODO, VJOURNAL, ...) is not converted at all —
    # without this, a feed of e.g. only VTODOs silently renders as a bare
    # "0 events" with no hint anything was even in the file (item 5).
    # VTIMEZONE/VALARM are definitional/nested plumbing, not skipped content,
    # so they're not counted here.
    skipped: Counter = Counter()
    for component in cal.walk():
        if component.name not in ("VCALENDAR", "VEVENT", "VTIMEZONE", "VALARM"):
            skipped[component.name] += 1

    calname = cal.get("X-WR-CALNAME")
    markdown = _build_markdown(events, str(calname) if calname else None, tz_name, skipped)
    return markdown, []
