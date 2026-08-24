"""Pure Markdown reading primitives: outline, section extraction, line
windowing, and text search — over a Markdown string already produced by
`es_doc_extract` (or any other Markdown, e.g. a vault note).

No I/O, no cache, no vault knowledge, no MCP wiring here — this module only
ever sees a string in and returns plain data out. Callers (the es_read tool,
via capabilities/reader.py) own fetching the Markdown, budget enforcement on
the SOURCE side, and any caching.

Why this exists: `es_doc_extract`'s converters deliberately emit one "## "
heading per unit of source structure (a PDF page, a spreadsheet sheet, a
docx heading, a calendar event) specifically so a reader can page through a
long document by heading instead of by raw character offset. This module is
that reader's engine.

Heading detection deliberately mirrors only the subset of Markdown our own
converters (and typical vault notes) actually produce: ATX headings
("## Title", 1-6 '#'s) and fenced code blocks (``` or ~~~, of any run
length >= 3, closed by a fence of the same character with length >= the
opener's). Setext headings ("Title\\n=====") are NOT treated as headings —
see the module-level note near _HEADING_RE for why.
"""
import re
from typing import List, Optional, TypedDict

PREAMBLE_ID = "preamble"

# ATX heading: 1-6 '#', at least one space, then the title text. Leading
# whitespace before the '#' is allowed (CommonMark permits up to 3 spaces of
# indent before a heading still counts as one; we're a little more lenient
# and allow any leading whitespace since it costs us nothing and several
# converters/editors indent consistently). A trailing run of '#'s (ATX
# "closing sequence", e.g. "## Title ##") is stripped from the title.
#
# Setext headings ("Title\n===") are NOT recognized. None of our own
# converters emit them, and a Word document imported to Markdown by some
# OTHER tool could contain a line of "---"/"===" that is meant as a
# thematic break or a table separator rather than a heading underline —
# treating every such line as a heading would be a false-positive machine.
# ATX is the unambiguous, converter-emitted form we actually need to page.
_HEADING_RE = re.compile(r"^[ \t]*(#{1,6})[ \t]+(.+?)[ \t]*$")
_TRAILING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")

# A fence-opening/closing line: >=3 of the same fence character, optionally
# followed by an "info string" (e.g. ```python) when OPENING. We don't need
# to distinguish opening-with-info from closing-bare for our purposes: we
# just track "are we currently inside a fence", toggling on any line whose
# fence run is long enough, using the SAME character as the block's opener.
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_WS_RE = re.compile(r"[\s_]+")


class NoSuchSection(Exception):
    es_code = "no_such_section"


class Section(TypedDict):
    id: str
    title: str
    level: int


def _slugify(title: str) -> str:
    """Turn a heading's raw text into a URL/id-friendly slug.

    Markdown emphasis/formatting characters (`**bold**`, `_em_`, backticks,
    a literal stray '#') are stripped rather than preserved verbatim: they're
    formatting, not identity, and keeping them would make ids like
    `**bold**-game` that are annoying to type back and fragile to punctuation
    choices. Whitespace/underscores collapse to a single '-'. An empty result
    (a heading that was ALL punctuation, e.g. "## ---") falls back to
    "section" so we never hand back an empty id.
    """
    text = _SLUG_STRIP_RE.sub("", title).strip().lower()
    text = _SLUG_WS_RE.sub("-", text)
    text = text.strip("-")
    return text or "section"


def _iter_headings(md: str):
    """Yield (line_index, level, title) for each ATX heading NOT inside a
    fenced code block. `md` is split on '\\n' after normalizing CRLF, so
    line_index is an index into that same list — callers that need the raw
    lines re-derive them the same way so indices line up."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fence_char: Optional[str] = None
    fence_len = 0
    for i, line in enumerate(lines):
        fm = _FENCE_RE.match(line)
        if fm:
            run = fm.group(1)
            ch, length = run[0], len(run)
            if fence_char is None:
                # Opening a new fence.
                fence_char, fence_len = ch, length
            elif ch == fence_char and length >= fence_len:
                # Closing the current fence (same character, run at least
                # as long as the opener — CommonMark's own closing rule).
                fence_char, fence_len = None, 0
            # A fence-looking line of the wrong character, or too short to
            # close, is just fence content while inside a block, and (rare)
            # a nested-looking marker while outside one — either way it is
            # not a heading, so no further handling needed here.
            continue
        if fence_char is not None:
            # Inside an (unclosed-or-not) fence: never a heading, including
            # a line that itself contains '#'s (e.g. a JSON blob's comment,
            # or an .ics DESCRIPTION line) — this is the whole point of
            # fence-tracking.
            continue
        hm = _HEADING_RE.match(line)
        if not hm:
            continue
        level = len(hm.group(1))
        title = _TRAILING_HASHES_RE.sub("", hm.group(2)).strip()
        if not title:
            # "## " with nothing but a closing "##" (e.g. "## ##") leaves no
            # title text; skip rather than yield a blank-titled section.
            continue
        yield i, level, title


def _assign_ids(titles: List[str]) -> List[str]:
    """Turn a list of heading titles (in document order) into distinct,
    stable ids: each title's slug, suffixed "-2", "-3", ... only as needed
    to stay unique.

    Uniqueness is checked against the set of ids ALREADY HANDED OUT, not
    just a per-slug occurrence counter — a naive counter (n-th time we see
    slug X -> "X-n") can still collide with an EARLIER, differently-titled
    heading whose own natural slug happens to equal that generated
    suffix. E.g. "Game", "Game 2", "Game" with a naive counter yields
    ids "game", "game-2", "game-2" (the second "Game"'s counter-based
    suffix collides with "Game 2"'s own slug) — two headings sharing one
    id, which breaks `section()` lookup silently (it returns whichever
    resolves first). Walking a growing `used` set and bumping the suffix
    past any collision, however produced, keeps ids one-to-one with
    headings no matter what the titles are.

    `used` is SEEDED with PREAMBLE_ID, not started empty: PREAMBLE_ID is
    already a reserved id (section() treats it as "text before the first
    heading" regardless of whether any heading resolves to it), so a
    document that happens to contain a heading literally titled "Preamble"
    must not be handed that same id — without the seed, `section("preamble")`
    would silently return the text before the first heading instead of the
    "## Preamble" heading's own body, and nothing about the collision would
    be visible to a caller.
    """
    used = {PREAMBLE_ID}
    ids = []
    for title in titles:
        slug = _slugify(title)
        candidate = slug
        n = 2
        while candidate in used:
            candidate = f"{slug}-{n}"
            n += 1
        used.add(candidate)
        ids.append(candidate)
    return ids


def outline(md: str) -> List[Section]:
    """List every heading in `md`, in document order, as
    {"id", "title", "level"}. An unclosed fence at EOF is treated as "still
    inside" for every remaining line (no heading past that point is picked
    up) — the same as a real Markdown renderer would treat it, and safer
    than guessing where it "should" have closed.
    """
    headings = list(_iter_headings(md))
    ids = _assign_ids([title for _i, _level, title in headings])
    return [
        {"id": sid, "title": title, "level": level}
        for sid, (_i, level, title) in zip(ids, headings)
    ]


def _lines_and_heading_positions(md: str):
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings = list(_iter_headings(md))  # (line_index, level, title)
    return lines, headings


def section(md: str, section_id: str) -> str:
    """Return the body text of the section identified by `section_id`
    (as produced by `outline`), or `PREAMBLE_ID` for the text before the
    first heading. The body RUNS TO the next heading of the same or
    shallower level (so a deeper subsection, e.g. an "###" under an "##",
    is included in its parent's body) and EXCLUDES the heading line itself.

    Raises NoSuchSection (naming the valid ids) if `section_id` doesn't
    match any heading and isn't PREAMBLE_ID.
    """
    lines, headings = _lines_and_heading_positions(md)

    if section_id == PREAMBLE_ID:
        end = headings[0][0] if headings else len(lines)
        return "\n".join(lines[:end]).strip("\n")

    # Recompute ids the same way outline() does, so we can locate the match
    # without requiring the caller to have called outline() first.
    ids = _assign_ids([title for _i, _level, title in headings])
    resolved = [
        (sid, i, level) for sid, (i, level, _title) in zip(ids, headings)
    ]  # (id, line_index, level)

    for idx, (sid, start_line, level) in enumerate(resolved):
        if sid != section_id:
            continue
        end = len(lines)
        for _sid2, other_line, other_level in resolved[idx + 1 :]:
            if other_level <= level:
                end = other_line
                break
        body_lines = lines[start_line + 1 : end]
        return "\n".join(body_lines).strip("\n")

    valid = ", ".join(r[0] for r in resolved) or "(no headings)"
    raise NoSuchSection(
        f"no section {section_id!r} in this document; valid ids: {valid}"
    )


class Window(TypedDict):
    lines: List[str]
    total_lines: int
    next_offset: Optional[int]


# 200 lines is comfortably under a token budget the agent can re-read a few
# times per turn without dominating context (typical converted-document
# lines run well under 200 chars; even at ~80 chars/line that's ~16,000
# chars, a fraction of a model's context), while still being enough to show
# useful continuous context in one call rather than forcing many round trips
# for an ordinary-sized section.
#
# This is NOT sized against the full document's own length — a converted
# document is only bounded by its own converter's resource ceiling now (tens
# of millions of characters for the flat formats; see doc_text.MAX_CHARS and
# friends), not by any fixed response-sized budget. The guarantee that one
# es_read call still comes back small is enforced one layer up, on this
# function's OUTPUT: mcp_server._CONTENT_CHAR_CAP caps `content` after
# window() returns it, independently of how large the source document is.
DEFAULT_WINDOW_LIMIT = 200


def window(md: str, offset: int = 0, limit: int = DEFAULT_WINDOW_LIMIT) -> Window:
    """Return `limit` lines of `md` starting at 0-based `offset`, plus
    `total_lines` and `next_offset` (None once there is nothing more to
    read) so a caller can keep paging without having to guess or re-derive
    the line count itself. An out-of-range offset returns an empty window,
    not an error — the agent doesn't know the line count in advance, and
    walking off the end is an ordinary way to discover that."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    total = len(lines)
    offset = max(offset, 0)
    chunk = lines[offset : offset + limit]
    next_off = offset + len(chunk)
    return {
        "lines": chunk,
        "total_lines": total,
        "next_offset": next_off if next_off < total else None,
    }


class LineHit(TypedDict):
    offset: int
    line: str


# Cap on how many {"offset","line"} entries a flat-content search returns (see
# `search` below). A needle that appears on nearly every line of a very long
# flat document (a repeated log-style token in a big .csv/.txt) would
# otherwise turn "find this" back into "here is most of the document again,
# one line at a time" — exactly the dump-it-all-at-once outcome offset
# paging exists to avoid. The agent can always search again with a more specific
# term, or page from the last reported offset, if it needs more than this.
_MAX_LINE_HITS = 50


def search(md: str, text: str) -> List[dict]:
    """Search `md` for `text`, case-insensitively, and return something the
    caller can act on next — the exact shape depends on whether `md` HAS
    headings:

    - If it does: return outline entries (same {"id","title","level"} shape
      as `outline`) for every section whose heading text or own body
      contains `text`, PLUS a {"id": PREAMBLE_ID, "title": "Preamble",
      "level": 0} entry first if the text before the first heading matches.
      Each entry feeds a follow-up `section(md, id)` call — the useful unit
      to hand back is exactly the id that call needs. Preamble text is
      bounded (it always ends at the first heading), so handing back its id
      the same way a real section's id is handed back costs nothing extra;
      it does not carry the "this might be huge" risk flat content does
      below.

    - If it has NO headings at all (flat content: a converted .csv/.txt/
      .json, or a plain-prose note) there is no section id to name, so
      matches are instead reported as up to `_MAX_LINE_HITS`
      {"offset", "line"} entries: the 0-based line number (to pass straight
      to `offset=`) and the matching line's own text for context. This
      shape is deliberately NOT the outline-entry shape — a caller can tell
      "this is a section id, call section()" apart from "this is a line
      offset, call offset=" just by which keys are present, with no extra
      flag needed. Returning `section(md, PREAMBLE_ID)` instead (the whole
      document, since a headingless document's "preamble" IS the whole
      document) would silently reintroduce the exact problem offset-paging
      exists to solve: dumping a possibly very large document back whole
      just because a search term was found somewhere inside it.

    An empty list means no match anywhere (headings, bodies, preamble, and
    — for flat content — every line); a non-empty list, in EITHER shape,
    means a match exists and names exactly where to look next.

    Case-insensitive because the agent is matching a user's own words
    (spoken/typed casually over Telegram) against document text it has
    never seen normalized — case is not meaningful signal here and a
    case-sensitive miss would silently look like "not in this document".

    "Own body" deliberately means the text up to the NEXT heading of ANY
    level — NOT `section()`'s same-or-shallower rule — so a match inside a
    nested subsection is attributed to that subsection alone, not also to
    every ancestor whose `section()` text happens to contain it because it
    includes the subsection. Without this, a search would report a parent
    section as "matching" purely because its printed body happens to embed
    a child heading's own matching text — redundant with the child hit and,
    worse, ordered ahead of it in outline order.
    """
    needle = text.lower()
    if not needle:
        return []
    lines, headings = _lines_and_heading_positions(md)

    if not headings:
        hits: List[LineHit] = []
        for i, line in enumerate(lines):
            if needle in line.lower():
                hits.append({"offset": i, "line": line})
                if len(hits) >= _MAX_LINE_HITS:
                    break
        return hits

    ids = _assign_ids([title for _i, _level, title in headings])
    hits: List[Section] = []
    preamble_end = headings[0][0]
    preamble_text = "\n".join(lines[:preamble_end])
    if needle in preamble_text.lower():
        hits.append({"id": PREAMBLE_ID, "title": "Preamble", "level": 0})
    for idx, (sid, (start_line, level, title)) in enumerate(zip(ids, headings)):
        entry: Section = {"id": sid, "title": title, "level": level}
        if needle in title.lower():
            hits.append(entry)
            continue
        end_line = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        own_body = "\n".join(lines[start_line + 1 : end_line])
        if needle in own_body.lower():
            hits.append(entry)
    return hits
