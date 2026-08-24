import inspect

import pytest

from es.capabilities import doc_ics


def _ics(tmp_path, body, name="feed.ics"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_each_event_becomes_its_own_heading(ics_file, tmp_path):
    """The whole point: a flat feed must come out as sections so the reader
    can page it. One 200KB section would defeat that."""
    md, images = doc_ics.convert(ics_file, tmp_path)
    headings = [l for l in md.splitlines() if l.startswith("## ")]
    assert len(headings) == 2
    assert "Game 1 vs Cedar Park Fury" in md
    assert "Game 2 vs Round Rock SC" in md
    assert images == []


def test_heading_carries_date_and_summary(ics_file, tmp_path):
    md, _ = doc_ics.convert(ics_file, tmp_path)
    head = [l for l in md.splitlines() if l.startswith("## ")][0]
    assert "Game 1" in head
    assert "2026" in head or "Sep" in head


def test_location_is_included(ics_file, tmp_path):
    md, _ = doc_ics.convert(ics_file, tmp_path)
    assert "Kelly Reeves" in md


def test_events_are_sorted_by_start_time(tmp_path):
    p = tmp_path / "unsorted.ics"
    p.write_text(
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Later\r\nDTSTART:20261001T100000Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Earlier\r\nDTSTART:20260901T100000Z\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n", encoding="utf-8")
    md, _ = doc_ics.convert(p, tmp_path)
    assert md.index("Earlier") < md.index("Later")


def test_malformed_ics_does_not_raise(tmp_path):
    p = tmp_path / "bad.ics"
    p.write_text("BEGIN:VCALENDAR\r\nthis is not valid\r\n", encoding="utf-8")
    md, _ = doc_ics.convert(p, tmp_path)
    assert isinstance(md, str)


def test_event_without_summary_or_dtstart_is_handled(tmp_path):
    p = tmp_path / "sparse.ics"
    p.write_text("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\nEND:VEVENT\r\n"
                 "END:VCALENDAR\r\n", encoding="utf-8")
    md, _ = doc_ics.convert(p, tmp_path)
    assert isinstance(md, str) and md.strip()


def test_all_day_event_renders_without_a_time(tmp_path):
    """DTSTART;VALUE=DATE has no time component — don't print a fake 00:00."""
    p = tmp_path / "allday.ics"
    p.write_text("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
                 "SUMMARY:Tournament Day\r\nDTSTART;VALUE=DATE:20260905\r\n"
                 "END:VEVENT\r\nEND:VCALENDAR\r\n", encoding="utf-8")
    md, _ = doc_ics.convert(p, tmp_path)
    assert "Tournament Day" in md
    assert "00:00" not in md


# A 500-event feed (with substantial per-event descriptions) is the
# design-flaw regression case: under the old 30,000-character
# conversion-time budget this lost everything past event ~219 —
# permanently, since nothing downstream (doc.md, es_read) ever saw the
# rest. It must now convert whole: the budget on what's RETURNED lives
# elsewhere (docs.py/es_read); this module only bounds what's genuinely
# absurd to store (see MAX_ICS_CHARS).
def test_large_feed_converts_in_full(tmp_path):
    p = tmp_path / "big.ics"
    events = "".join(
        f"BEGIN:VEVENT\r\nUID:{i}\r\nSUMMARY:Game {i}\r\n"
        f"DTSTART:2026090{i % 9 + 1}T140000Z\r\n"
        f"DESCRIPTION:{'x' * 400}\r\nEND:VEVENT\r\n" for i in range(500))
    p.write_text("BEGIN:VCALENDAR\r\n" + events + "END:VCALENDAR\r\n", encoding="utf-8")
    md, _ = doc_ics.convert(p, tmp_path)
    assert "truncated" not in md.lower()
    headings = [l for l in md.splitlines() if l.startswith("## ")]
    assert len(headings) == 500
    assert "Game 499" in md


# The resource ceiling (MAX_ICS_CHARS) still exists for a genuinely absurd
# feed — monkeypatched down here so the test doesn't need to generate
# millions of real characters to exercise it.
def test_ics_resource_ceiling_truncates_at_an_event_boundary_with_an_honest_marker(
        tmp_path, monkeypatch):
    monkeypatch.setattr(doc_ics, "MAX_ICS_CHARS", 2_000)
    p = tmp_path / "big.ics"
    events = "".join(
        f"BEGIN:VEVENT\r\nUID:{i}\r\nSUMMARY:Game {i}\r\n"
        f"DTSTART:2026090{i % 9 + 1}T140000Z\r\n"
        f"DESCRIPTION:{'x' * 400}\r\nEND:VEVENT\r\n" for i in range(500))
    p.write_text("BEGIN:VCALENDAR\r\n" + events + "END:VCALENDAR\r\n", encoding="utf-8")
    md, _ = doc_ics.convert(p, tmp_path)
    assert "truncated" in md.lower()
    # never cut mid-event: the last heading must have its body intact
    assert not md.rstrip().endswith("## ")
    # Content past the ceiling was never converted/cached at all — the
    # marker must say so plainly, not imply it's reachable another way.
    assert "no page range to resume from" in md
    assert "narrower date range or a smaller export" in md


def test_event_count_is_reported(ics_file, tmp_path):
    md, _ = doc_ics.convert(ics_file, tmp_path)
    assert "2" in md.splitlines()[0]


# --- item 1: timezone conversion correctness (was completely untested) -----

def test_utc_dtstart_converts_to_home_timezone_cdt(ics_file, tmp_path):
    """schedule.ics's Game 1 is DTSTART:20260905T140000Z. September is CDT
    (America/Chicago, UTC-5) -> 14:00 UTC must render as 9:00 AM local, not
    the raw UTC hour."""
    md, _ = doc_ics.convert(ics_file, tmp_path)
    head = [l for l in md.splitlines() if l.startswith("## ")][0]
    assert "9:00 AM" in head
    assert "2:00 PM" not in head  # the raw (wrong) UTC-as-local hour


def test_utc_dtstart_converts_to_home_timezone_cst_in_winter(tmp_path):
    """Pin the other side of the DST transition: a December UTC timestamp
    must use CST (UTC-6), not CDT — 14:00 UTC -> 8:00 AM, not 9:00 AM."""
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Winter Game\r\nDTSTART:20261220T140000Z\r\n"
              "END:VEVENT\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    head = [l for l in md.splitlines() if l.startswith("## ")][0]
    assert "8:00 AM" in head
    assert "9:00 AM" not in head


def test_tzid_event_converts_to_home_timezone(tmp_path):
    """A DTSTART with an explicit TZID (not UTC/'Z') must also be converted
    to the operator's home zone, not left in the feed's own zone. 9:00 AM
    America/New_York (EDT, UTC-4) is 8:00 AM America/Chicago (CDT, UTC-5)."""
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:NY Event\r\n"
              "DTSTART;TZID=America/New_York:20260905T090000\r\n"
              "END:VEVENT\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    head = [l for l in md.splitlines() if l.startswith("## ")][0]
    assert "8:00 AM" in head
    assert "9:00 AM" not in head


def test_floating_time_event_is_not_converted(tmp_path):
    """A DTSTART with neither 'Z' nor TZID is a floating time (RFC 5545) —
    there is no zone to convert it FROM, so it must render exactly as
    written rather than being (wrongly) treated as UTC or as already-local
    in some other way."""
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Floating Event\r\nDTSTART:20260905T111500\r\n"
              "END:VEVENT\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    head = [l for l in md.splitlines() if l.startswith("## ")][0]
    assert "11:15 AM" in head


# --- item 2: every heading is self-contained with an explicit year --------

def test_every_heading_includes_the_year(tmp_path):
    """A season-spanning feed (Dec 2026 -> Jan 2027) must carry the year in
    EVERY heading, not just where it changes: the reader pages by heading,
    so a lone "## Sun Jan 10, 8:00 AM" section (no neighbors visible) must
    not require guessing the year."""
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Game 1\r\nDTSTART:20261220T140000Z\r\n"
              "END:VEVENT\r\n"
              "BEGIN:VEVENT\r\nUID:2\r\nSUMMARY:Game 2\r\n"
              "DTSTART:20270110T140000Z\r\nEND:VEVENT\r\n"
              "END:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    headings = [l for l in md.splitlines() if l.startswith("## ")]
    assert len(headings) == 2
    assert "2026" in headings[0]
    assert "2027" in headings[1]


# --- item 3: a multi-day event shows its end DATE, not just end time ------

def test_multiday_event_shows_end_date(tmp_path):
    """A 3-day tournament (Sat -> Mon) must not read as ending the same
    evening it starts."""
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Tournament\r\nDTSTART:20260905T140000Z\r\n"
              "DTEND:20260907T230000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    assert "Ends Mon Sep 7, 2026, 6:00 PM" in md


def test_same_day_event_end_still_shows_time_only(ics_file, tmp_path):
    """Regression: an ordinary same-day event (the existing fixture, 9:00 AM
    -> 10:30 AM) must NOT grow a redundant date on its end line."""
    md, _ = doc_ics.convert(ics_file, tmp_path)
    assert "Ends 10:30 AM" in md
    assert "Ends Sat Sep 5" not in md


# --- item 4: RRULE is never silently dropped -------------------------------

def test_rrule_with_count_is_annotated(tmp_path):
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Practice\r\nDTSTART:20260905T140000Z\r\n"
              "RRULE:FREQ=WEEKLY;COUNT=30\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    assert "(repeats weekly, 30 times)" in md
    # the header must not silently claim just "1 event" for a season-long series
    assert "1 event (1 recurring)" in md.splitlines()[0]


def test_rrule_with_until_is_annotated(tmp_path):
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Practice\r\nDTSTART:20260905T140000Z\r\n"
              "RRULE:FREQ=WEEKLY;INTERVAL=2;UNTIL=20261220T140000Z\r\n"
              "END:VEVENT\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    assert "(repeats every 2 weeks, until Dec 20, 2026)" in md


def test_rrule_with_no_end_is_annotated(tmp_path):
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Practice\r\nDTSTART:20260905T140000Z\r\n"
              "RRULE:FREQ=MONTHLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    assert "(repeats monthly, no end date)" in md


def test_non_recurring_event_has_no_repeat_note(ics_file, tmp_path):
    md, _ = doc_ics.convert(ics_file, tmp_path)
    assert "repeats" not in md
    assert "recurring" not in md.splitlines()[0]


# --- item 5: non-VEVENT content is not silently dropped --------------------

def test_vtodo_only_feed_reports_what_was_skipped(tmp_path):
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:1\r\n"
              "SUMMARY:Buy shin guards\r\nEND:VTODO\r\n"
              "BEGIN:VTODO\r\nUID:2\r\nSUMMARY:Renew membership\r\n"
              "END:VTODO\r\nEND:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    first_line = md.splitlines()[0]
    assert "0 events" in first_line
    assert "2 VTODOs" in first_line
    assert "not shown" in first_line


def test_mixed_feed_reports_events_and_skipped_todos(tmp_path):
    p = _ics(tmp_path,
              "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\n"
              "SUMMARY:Game 1\r\nDTSTART:20260905T140000Z\r\n"
              "END:VEVENT\r\nBEGIN:VTODO\r\nUID:2\r\n"
              "SUMMARY:Renew membership\r\nEND:VTODO\r\n"
              "END:VCALENDAR\r\n")
    md, _ = doc_ics.convert(p, tmp_path)
    first_line = md.splitlines()[0]
    assert "1 event" in first_line
    assert "1 VTODO" in first_line
    assert "Game 1" in md


# --- item 6: stale constant reference --------------------------------------

def test_no_stale_max_csv_chars_reference():
    """doc_text.MAX_CSV_CHARS was renamed MAX_CHARS; doc_ics must not still
    reference the old, now-nonexistent name (even in a comment)."""
    src = inspect.getsource(doc_ics)
    assert "MAX_CSV_CHARS" not in src
