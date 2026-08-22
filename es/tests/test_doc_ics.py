import pytest

from es.capabilities import doc_ics


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


def test_large_feed_is_truncated_at_an_event_boundary(tmp_path):
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


def test_event_count_is_reported(ics_file, tmp_path):
    md, _ = doc_ics.convert(ics_file, tmp_path)
    assert "2" in md.splitlines()[0]
