"""Pure Markdown reading primitives: outline, section extraction, line
windowing, and text query — over a Markdown string already produced by
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
    """
    used = set()
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


# es_doc_extract's own conversion budget is 40_000 characters
# (docs.py:MAX_MARKDOWN_CHARS); a full document `window` might be asked to
# page through is bounded by roughly that size. 200 lines is comfortably
# under a token budget the agent can re-read a few times per turn without
# dominating context (typical converted-document lines run well under 200
# chars; even at ~80 chars/line that's ~16,000 chars, a fraction of a
# model's context and a fraction of the 40k source budget), while still
# being enough to show useful continuous context in one call rather than
# forcing many round trips for an ordinary-sized section.
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


def query(md: str, text: str) -> List[Section]:
    """Return the outline entries (same shape as `outline`) for every
    section whose HEADING TEXT or OWN BODY contains `text`, case-insensitively.

    Case-insensitive because the agent is matching a user's own words
    (spoken/typed casually over Telegram) against document text it has
    never seen normalized — case is not meaningful signal here and a
    case-sensitive miss would silently look like "not in this document".

    Returns whole outline entries (id/title/level), not matching lines with
    surrounding context: the result feeds a follow-up `section(md, id)`
    call, so the useful unit to hand back is exactly the id that call
    needs, and duplicating a snippet of body text here would be a second,
    inconsistent view of content the agent can just read in full a moment
    later for the cost of one more call.

    "Own body" deliberately means the text up to the NEXT heading of ANY
    level — NOT `section()`'s same-or-shallower rule — so a match inside a
    nested subsection is attributed to that subsection alone, not also to
    every ancestor whose `section()` text happens to contain it because it
    includes the subsection. Without this, a query would report a parent
    section as "matching" purely because its printed body happens to embed
    a child heading's own matching text — redundant with the child hit and,
    worse, ordered ahead of it in outline order.
    """
    needle = text.lower()
    if not needle:
        return []
    lines, headings = _lines_and_heading_positions(md)
    if not headings:
        return []
    ids = _assign_ids([title for _i, _level, title in headings])
    hits: List[Section] = []
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
