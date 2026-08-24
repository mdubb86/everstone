"""Plain text, Markdown, CSV, and JSON documents.

None of these four formats have pages: this module deliberately does NOT
implement `page_count`/`render` — their absence on a converter module IS the
capability signal docs.py's dispatch relies on (see docs.py's `_page_count`
docstring), telling the rest of docs.py "this format has no pages" without a
separate hardcoded format list anywhere.

IMPORTANT for whoever builds the future pageable-by-"##"-heading reader: do
NOT retrofit synthetic headings into this module's output just to make these
formats pageable that way. A CSV is a table, a .txt file is flat prose, a
.json file is a blob — none of them have real document structure to hang a
heading off of, and inventing one (e.g. "## Rows 1-100") would be a
fabrication, not a description of the source, and would corrupt the one
signal ("a `##` heading marks a real section") the reader is meant to trust.
Flat content like this is exactly what that reader's plain offset/limit
fallback exists for — leave it to that, don't paper over it here.

Truncation seam: none of these four formats has PDF-style "## Page N"
boundaries to cut at (a paginated PDF's own truncation, where it applies,
lives in doc_pdf.py), so every converter in this module truncates ITSELF, at
a boundary meaningful to its own format (a whole CSV row, a whole line of
text/JSON), before returning. See MAX_CHARS below.

MAX_CHARS is a RESOURCE ceiling, not a context-window budget: the whole
converted document is written to `doc.md` (a 24h-TTL cache) and paged from
there by es_read, so bounding what gets STORED to fit inside one MCP
response would only destroy data nothing needs destroyed — es_doc_extract's
own response is now a small receipt (a fixed-size preview plus a `doc:<id>`
handle), never the document itself, so it has no response-sized budget of
its own to protect. MAX_CHARS below exists only to stop a genuinely
pathological document from writing an unbounded amount to disk / making
es_read's per-heading outline unusably large — see the constant's own
comment.
"""
import csv
import io
import json
from pathlib import Path
from typing import List, Optional, Tuple

from es.capabilities.doc_support import (ParseFailed, format_cell, format_row,
                                          truncation_marker)

# Mirrors docs.CSV_FIELD_SIZE_LIMIT (10 MiB) — used ONLY for this module's own
# ParseFailed message wording below, not to configure the csv module itself
# (docs.py owns that process-wide call). Duplicated rather than imported for
# the same reason MAX_CHARS is duplicated below: docs.py imports this module
# at load time to populate CONVERTERS, so this module importing back from
# docs.py to read the real constant would run while docs.py is still
# mid-import. Keep the two values in sync by hand if either changes.
_CSV_FIELD_SIZE_LIMIT_MB = 10

# Character budget shared by every converter in this module (CSV rows,
# text/Markdown/JSON lines), enforced by truncating at a whole-ROW or
# whole-LINE boundary — never mid-row/mid-line, since a half-written one
# reads as corrupt data, not as "there's more".
#
# This is a RESOURCE ceiling ("this document is absurd"), not a
# context-window budget — that distinction is the whole reason this number
# is no longer 30_000. It used to be sized to land under
# docs.MAX_MARKDOWN_CHARS (40_000) back when es_doc_extract's response was
# the only thing that ever saw this converter's output; now the full result
# is written to `doc.md` (a 24h-TTL cache) and paged by es_read, so a
# context-sized cap here just throws away rows/lines nothing needed thrown
# away — measured live, a 3,000-row CSV lost everything past row ~1,300.
#
# Sized against the one real constraint upstream of this module: a document
# larger than docs.MAX_DOCUMENT_BYTES (50 MB) is refused before conversion
# ever runs. For CSV/text/Markdown, this module's own Markdown output runs
# close to 1:1 with the source's byte size (the pipe-table format adds a
# few characters of punctuation per cell, not per-cell padding), so a 50 MB
# input converts to roughly 50 MB of Markdown. JSON is the one format that
# can genuinely grow under conversion — re-serializing with `indent=2` adds
# real bytes a minified source didn't have — but even a multi-times blowup
# of a 50 MB input lands in the tens of millions of characters, not
# hundreds. 20_000_000 (20M characters, ~20 MB) sits well above any
# document this module will realistically ever see (a 3,000-row roster
# export is a few hundred KB; a 40,000-row CSV export is still only a few
# MB) while remaining a real, bounded ceiling: at 20 MB, `doc.md`'s disk
# cost and es_read's outline stay reasonable even in the worst realistic
# case, and something that still exceeds it is genuinely pathological, not
# just "a large document".
#
# Duplicated here rather than imported from docs.py, because docs.py
# imports this module at load time to populate CONVERTERS, and this module
# importing back from docs.py to read MAX_DOCUMENT_BYTES would run while
# docs.py is still mid-import. Keep the two in sync by hand if either
# changes.
MAX_CHARS = 20_000_000

# The remedy for hitting MAX_CHARS is the same non-answer for every one of
# these formats: unlike a PDF's `pages="N-M"`, there is no sub-range these
# flat formats support re-requesting narrower — so every marker below says
# that explicitly rather than gesturing at a resume mechanism that doesn't
# exist for this format. Content past MAX_CHARS genuinely never exists
# anywhere (it is cut before `doc.md` is ever written) — unlike es_read's
# own separate per-call content cap (mcp_server._CONTENT_CHAR_CAP), which the
# agent CAN page past via `offset` — so "no resume" stays an honest thing for
# this marker to say even though the document as a whole (up to MAX_CHARS)
# is otherwise fully cached and pageable.
_NO_RESUME = "has no page range to resume from"


def _truncate_at_line_boundary(text: str, limit: int) -> Tuple[str, bool, int, int]:
    """Cut `text` to at most `limit` characters at the last LINE boundary
    (the last "\\n") at or before the limit — never mid-line.

    Returns (kept_text, truncated, kept_line_count, total_line_count).
    kept_line_count is 0 when even the FIRST line alone exceeds `limit`:
    there is no earlier line boundary to cut at in that case, so this falls
    back to a hard character cut (mirroring doc_pdf's own "even page 1
    alone exceeds the limit" fallback) — the caller's marker must say so
    rather than implying a line count that isn't real.
    """
    if len(text) <= limit:
        return text, False, 0, 0
    total_lines = text.count("\n") + 1
    cut = text.rfind("\n", 0, limit)
    if cut == -1:
        return text[:limit], True, 0, total_lines
    kept_lines = text.count("\n", 0, cut) + 1
    return text[:cut], True, kept_lines, total_lines


def _line_truncation_marker(kept_lines: int, total_lines: int, kind: str) -> str:
    if kept_lines == 0:
        return "\n\n" + truncation_marker(
            f"inside the first line — it alone exceeds "
            f"the {MAX_CHARS}-character limit, so there is no earlier "
            f"line boundary to cut at; this {kind} file {_NO_RESUME}, "
            "so ask for a narrower excerpt if the rest is needed")
    return "\n\n" + truncation_marker(
        f"after {kept_lines} of {total_lines} lines — "
        f"the {MAX_CHARS}-character limit was reached; this {kind} "
        f"file {_NO_RESUME}, so ask for a narrower excerpt, or the "
        "rest of the file, if more is needed")


def _read_text(source: Path) -> str:
    # errors="replace" rather than raising or auto-detecting an encoding:
    # these files arrive from outside (Telegram uploads, vault attachments)
    # with no guarantee of UTF-8. A CSV from, say, a European club's export
    # tool in Latin-1 will come through with a handful of U+FFFD replacement
    # characters in place of accented letters — visibly odd but still
    # mostly readable, and the document loads instead of hard-failing. This
    # module does not attempt encoding detection/re-decoding: guessing wrong
    # silently would be worse than a visible replacement character, and a
    # real "wrong encoding" fix belongs at a layer that can ask the user
    # which encoding to use, not to a best-effort convert().
    return source.read_text(encoding="utf-8", errors="replace")


def _safe_csv_rows(reader, source: Path):
    """Wrap ONLY the underlying csv.reader's own `next()` call. csv.reader is
    LAZY — verified empirically: neither "a field larger than the size
    limit" nor "an unbalanced quote runs to the end of the file" raises
    `csv.Error` at `csv.reader(...)` construction, only while actually
    ITERATING it (each `next()` call parses one more row's worth of the
    underlying text). This function is the tightest boundary that still
    catches the failure: the caller's own row-processing (the `if row`
    filter, format_cell/format_row) stays entirely outside this function and
    its try block, reached only via the rows this generator yields — a bug
    in that logic can never be caught here and relabeled "corrupt CSV"."""
    while True:
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error as e:
            raise ParseFailed(
                f"{source.name} could not be parsed as a CSV — a field is "
                f"larger than the {_CSV_FIELD_SIZE_LIMIT_MB}MB limit, or an "
                "unbalanced quote runs to the end of the file; ask the user "
                "to resend it or export a narrower/cleaner version") from e
        yield row


def _convert_csv(source: Path) -> str:
    text = _read_text(source)
    # csv.reader needs a real line iterator, not pre-split lines: a quoted
    # field may legitimately contain an embedded newline (RFC 4180), and
    # text.splitlines() would break that field's content across two "rows"
    # before csv.reader ever gets a chance to see the surrounding quotes.
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in _safe_csv_rows(reader, source) if row]
    if not rows:
        return ""

    esc_rows = [[format_cell(c) for c in row] for row in rows]
    width = max(len(r) for r in esc_rows)
    header, *data_rows = esc_rows

    lines = [format_row(header, width),
             "|" + "|".join([" --- "] * width) + "|"]
    used = len("\n".join(lines))
    kept = 0
    for row in data_rows:
        line = format_row(row, width)
        cost = len(line) + 1  # + the newline joining it to the previous line
        if used + cost > MAX_CHARS:
            break
        lines.append(line)
        used += cost
        kept += 1

    md = "\n".join(lines)
    if kept < len(data_rows):
        md += "\n\n" + truncation_marker(
            f"after {kept} of {len(data_rows)} data rows "
            f"— the {MAX_CHARS}-character limit was reached; this CSV "
            f"{_NO_RESUME}, so ask for a narrower export, or filter it, "
            "if the rest is needed")
    return md


def _convert_json(source: Path) -> str:
    text = _read_text(source)
    # Both json.loads AND json.dumps belong inside this one try: neither is
    # lazy, and there is no rendering logic of OUR OWN in between them to
    # accidentally shield — this is two adjacent stdlib calls performing one
    # logical "parse, then re-format for display" step. Verified empirically
    # that BOTH can raise RecursionError for the identical root cause (a
    # document nested far deeper than any hand-authored file would be, ~1000
    # levels): json.loads's C accelerator has enough headroom to parse a
    # depth that json.dumps's pure-Python encoder (forced by `indent=2`,
    # which has no C-accelerated path) then fails to re-serialize — so a
    # document exists that loads cleanly but still blows the recursion limit
    # one line later. Catching RecursionError only around loads() would
    # leave that real, reachable case leaking a raw RecursionError.
    try:
        parsed = json.loads(text)
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    except RecursionError as e:
        raise ParseFailed(
            f"{source.name} is nested too deeply to parse as JSON; ask the "
            "user to resend a flatter export") from e
    except ValueError:
        # Invalid JSON: hand back the raw text rather than raising — the
        # agent can still read it (and tell the user what's wrong with it),
        # which a hard failure would prevent.
        pretty = text

    kept, truncated, kept_lines, total_lines = _truncate_at_line_boundary(pretty, MAX_CHARS)
    # The fence is built from `kept` (a prefix of `pretty`, cut at a line
    # boundary) and ALWAYS closed here, before the marker is appended below
    # — so a truncated fenced block can never be left open. An unclosed
    # ```json fence would swallow every line the agent reads after it as
    # "still inside the code block", corrupting the rest of the document,
    # not just the cut portion.
    md = f"```json\n{kept}\n```"
    if truncated:
        md += _line_truncation_marker(kept_lines, total_lines, "JSON")
    return md


def _convert_plain(source: Path, kind: str) -> str:
    text = _read_text(source)
    kept, truncated, kept_lines, total_lines = _truncate_at_line_boundary(text, MAX_CHARS)
    if truncated:
        kept += _line_truncation_marker(kept_lines, total_lines, kind)
    return kept


_HANDLERS = {
    ".csv": _convert_csv,
    ".json": _convert_json,
    ".txt": lambda source: _convert_plain(source, "text"),
    ".md": lambda source: _convert_plain(source, "markdown"),
}


def convert(source: Path, adir: Path,
            pages: Optional[List[int]] = None, **_ignored) -> Tuple[str, List[Path]]:
    """Return (markdown, []) — none of these formats produce images.

    `pages` is accepted (matching doc_pdf.convert's signature, which
    docs.extract calls positionally-by-keyword the same way for every
    converter) but unused: none of these formats has pages, and docs.py
    already raises InvalidPageRange before calling convert() if the caller
    passed an explicit pages= for a format whose module has no page_count
    (see docs.py's _page_count / extract()). `**_ignored` absorbs any other
    keyword docs.extract may pass in the future without this module needing
    to change in lockstep.
    """
    handler = _HANDLERS[source.suffix.lower()]
    return handler(source), []
