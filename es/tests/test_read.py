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
    hits = read.search(DOC, "second")
    assert len(hits) == 1 and hits[0]["title"] == "Page 2"


def test_query_matches_heading_text_too():
    assert read.search(DOC, "Sub of")[0]["title"] == "Sub of page 2"


def test_query_with_no_hits_returns_empty():
    assert read.search(DOC, "zebra") == []


def test_document_with_no_headings_has_an_empty_outline():
    assert read.outline("just\nsome\nlines\n") == []


# --- defect 1: PREAMBLE_ID must not collide with a real "## Preamble" heading ---


PREAMBLE_COLLISION_DOC = (
    "TEXT BEFORE ANY HEADING\n\n"
    "## Preamble\n\n"
    "THE REAL PREAMBLE SECTION\n\n"
    "## Next\n\n"
    "Next body.\n"
)


def test_heading_literally_named_preamble_does_not_take_the_reserved_id():
    out = read.outline(PREAMBLE_COLLISION_DOC)
    ids = [s["id"] for s in out]
    assert ids[0] != read.PREAMBLE_ID, "the real heading must not steal the reserved id"
    assert read.PREAMBLE_ID not in ids, "the reserved id must stay free for the true preamble"


def test_reserved_preamble_id_still_reaches_the_true_preamble_text():
    assert "TEXT BEFORE ANY HEADING" in read.section(
        PREAMBLE_COLLISION_DOC, read.PREAMBLE_ID
    )


def test_heading_named_preamble_is_reachable_by_its_own_distinct_id():
    out = read.outline(PREAMBLE_COLLISION_DOC)
    heading_id = out[0]["id"]
    assert out[0]["title"] == "Preamble"
    body = read.section(PREAMBLE_COLLISION_DOC, heading_id)
    assert "THE REAL PREAMBLE SECTION" in body


def test_query_for_the_real_preamble_section_resolves_to_its_own_id_not_the_true_preamble():
    hits = read.search(PREAMBLE_COLLISION_DOC, "THE REAL")
    assert len(hits) == 1
    assert hits[0]["id"] != read.PREAMBLE_ID
    assert hits[0]["title"] == "Preamble"


# --- defect 2: query must see flat content and preamble text ---


def test_query_matches_preamble_text_in_a_document_that_also_has_headings():
    hits = read.search(DOC, "Intro line")
    assert hits == [{"id": read.PREAMBLE_ID, "title": "Preamble", "level": 0}]


def test_query_over_flat_content_with_no_headings_returns_line_offsets():
    doc = "\n".join([
        "the quick brown fox",
        "Acme delivered the package",
        "nothing to see here",
        "another Acme mention",
    ])
    hits = read.search(doc, "acme")
    assert hits == [
        {"offset": 1, "line": "Acme delivered the package"},
        {"offset": 3, "line": "another Acme mention"},
    ]


def test_query_over_flat_content_with_no_match_is_still_an_empty_list():
    doc = "the quick brown fox\nnothing else\n"
    assert read.search(doc, "zebra") == []


def test_query_flat_content_hits_are_distinguishable_from_sectioned_hits():
    flat_hits = read.search("Acme appears here.\nAcme again.\n", "acme")
    assert all("offset" in h and "id" not in h for h in flat_hits)

    sectioned_hits = read.search(DOC, "second")
    assert all("id" in h and "offset" not in h for h in sectioned_hits)


def test_query_over_flat_content_caps_the_number_of_line_hits():
    doc = "\n".join("acme mention" for _ in range(500))
    hits = read.search(doc, "acme")
    assert len(hits) == read._MAX_LINE_HITS


# --- defect 3: _assign_ids must not collide when an unrelated heading's ---
# --- natural slug equals a generated dedup suffix ---


def test_assign_ids_avoids_colliding_with_an_unrelated_headings_natural_slug():
    # A naive per-slug counter gives "game", "game-2", "game-2" here: the
    # second "Game" is the counter's 2nd occurrence, so it gets suffix "-2",
    # which collides with the OTHER heading "Game 2"'s own natural slug.
    ids = read._assign_ids(["Game", "Game 2", "Game"])
    assert ids == ["game", "game-2", "game-3"]
    assert len(set(ids)) == 3


def test_assign_ids_avoids_colliding_when_a_natural_slug_equals_a_deduped_id():
    # "Game" appears twice (dedups to "game", "game-2"), and a THIRD,
    # differently-titled heading's own natural slug is "game-2" — it must
    # not silently share the id already claimed by the second "Game".
    ids = read._assign_ids(["Game", "Game", "Game 2"])
    assert len(set(ids)) == 3
    assert ids[0] == "game"


def test_assign_ids_treats_case_and_punctuation_variants_as_distinct_slugs_that_can_still_collide():
    # "Game!" and "Game" slugify to the same "game" text, so they must still
    # be deduped against each other like any other repeated slug.
    ids = read._assign_ids(["Game!", "GAME", "Game"])
    assert len(set(ids)) == 3
    assert ids[0] == "game"
