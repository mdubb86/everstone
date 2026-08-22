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

Truncation seam: docs._truncate_markdown assumes paginated PDF output (it
cuts at "## Page N" boundaries) and none of these formats emit those
markers, so it cannot truncate them sensibly — every converter in this
module truncates ITSELF, at a boundary meaningful to its own format (a whole
CSV row, a whole line of text/JSON), before returning. See MAX_CHARS below.
"""
import csv
import io
import json
from pathlib import Path
from typing import List, Optional, Tuple

from es.capabilities.doc_support import format_cell, format_row

# Character budget shared by every converter in this module (CSV rows,
# text/Markdown/JSON lines), enforced by truncating at a whole-ROW or
# whole-LINE boundary — never mid-row/mid-line, since a half-written one
# reads as corrupt data, not as "there's more". Chosen to land comfortably
# inside docs.MAX_MARKDOWN_CHARS (40_000): duplicated here rather than
# imported, because docs.py imports this module at load time to populate
# CONVERTERS, and this module importing back from docs.py to read that
# constant would run while docs.py is still mid-import. Keep the two in
# sync by hand if either changes.
#
# A plain COUNT cap alone (e.g. "first 2000 rows"/"first 2000 lines") was
# rejected: a wide CSV or a file of long lines can blow the character budget
# in far fewer than 2000, and a narrow/short one could safely fit many more
# — the actual risk this cap manages is markdown size landing in the
# agent's context, not row/line count. A 40,000-row CSV export, or a
# multi-megabyte JSON/log file, is entirely plausible in the real world; the
# agent's context is not sized for it, so once the budget is spent this
# simply stops and says so, rather than pretending a full dump is fine.
MAX_CHARS = 30_000

# The remedy for hitting MAX_CHARS is the same non-answer for every one of
# these formats: unlike a PDF's `pages="N-M"`, there is no sub-range these
# flat formats support re-requesting narrower — so every marker below says
# that explicitly rather than gesturing at a resume mechanism that doesn't
# exist for this format (the same honesty docs._truncate_markdown itself
# uses for a PDF's oversized first page).
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
        return (f"\n\n*(truncated inside the first line — it alone exceeds "
                 f"the {MAX_CHARS}-character limit, so there is no earlier "
                 f"line boundary to cut at; this {kind} file {_NO_RESUME}, "
                 "so ask for a narrower excerpt if the rest is needed)*")
    return (f"\n\n*(truncated after {kept_lines} of {total_lines} lines — "
             f"the {MAX_CHARS}-character limit was reached; this {kind} "
             f"file {_NO_RESUME}, so ask for a narrower excerpt, or the "
             "rest of the file, if more is needed)*")


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


def _convert_csv(source: Path) -> str:
    text = _read_text(source)
    # csv.reader needs a real line iterator, not pre-split lines: a quoted
    # field may legitimately contain an embedded newline (RFC 4180), and
    # text.splitlines() would break that field's content across two "rows"
    # before csv.reader ever gets a chance to see the surrounding quotes.
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
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
        md += (f"\n\n*(truncated after {kept} of {len(data_rows)} data rows "
               f"— the {MAX_CHARS}-character limit was reached; this CSV "
               f"{_NO_RESUME}, so ask for a narrower export, or filter it, "
               "if the rest is needed)*")
    return md


def _convert_json(source: Path) -> str:
    text = _read_text(source)
    try:
        parsed = json.loads(text)
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
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
