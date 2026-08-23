import pytest

from es.capabilities import read

DOC = """Intro line before any heading.

## Page 1

First page body.

## Page 2

Second page body.

### Sub of page 2

Nested content.

## Page 3

Third.
"""


def test_outline_lists_headings_with_levels_and_ids():
    out = read.outline(DOC)
    assert [s["title"] for s in out] == ["Page 1", "Page 2", "Sub of page 2", "Page 3"]
    assert [s["level"] for s in out] == [2, 2, 3, 2]
    assert all(s["id"] for s in out)


def test_section_returns_its_body_and_stops_at_the_next_same_level_heading():
    body = read.section(DOC, "page-2")
    assert "Second page body." in body
    assert "Nested content." in body, "a subsection belongs to its parent"
    assert "Third." not in body


def test_preamble_before_the_first_heading_is_reachable():
    assert "Intro line" in read.section(DOC, read.PREAMBLE_ID)


def test_duplicate_headings_get_distinct_ids():
    doc = "## Game\n\nA\n\n## Game\n\nB\n"
    out = read.outline(doc)
    assert out[0]["id"] != out[1]["id"]
    assert "A" in read.section(doc, out[0]["id"])
    assert "B" in read.section(doc, out[1]["id"])


def test_unknown_section_id_raises_and_names_valid_ones():
    with pytest.raises(read.NoSuchSection) as e:
        read.section(DOC, "nope")
    assert "page-1" in str(e.value)


def test_headings_inside_fenced_code_are_not_sections():
    doc = "## Real\n\n```\n## Not a heading\n```\n\n## Also real\n"
    assert [s["title"] for s in read.outline(doc)] == ["Real", "Also real"]


def test_tilde_fences_are_also_respected():
    doc = "## Real\n\n~~~\n## Not a heading\n~~~\n\n## Also real\n"
    assert [s["title"] for s in read.outline(doc)] == ["Real", "Also real"]


def test_offset_window_returns_lines_and_reports_total():
    doc = "\n".join(f"line {i}" for i in range(100))
    win = read.window(doc, offset=10, limit=5)
    assert win["lines"] == ["line 10", "line 11", "line 12", "line 13", "line 14"]
    assert win["total_lines"] == 100
    assert win["next_offset"] == 15


def test_window_past_the_end_is_empty_not_an_error():
    win = read.window("a\nb\n", offset=50, limit=5)
    assert win["lines"] == [] and win["next_offset"] is None


def test_window_at_the_exact_end_reports_no_next_offset():
    win = read.window("\n".join(f"l{i}" for i in range(10)), offset=5, limit=5)
    assert len(win["lines"]) == 5 and win["next_offset"] is None


def test_query_returns_matching_sections_with_ids():
    hits = read.query(DOC, "second")
    assert len(hits) == 1 and hits[0]["title"] == "Page 2"


def test_query_matches_heading_text_too():
    assert read.query(DOC, "Sub of")[0]["title"] == "Sub of page 2"


def test_query_with_no_hits_returns_empty():
    assert read.query(DOC, "zebra") == []


def test_document_with_no_headings_has_an_empty_outline():
    assert read.outline("just\nsome\nlines\n") == []
