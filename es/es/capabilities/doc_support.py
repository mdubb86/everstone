"""Shared Markdown-table rendering.

Used by every converter that turns tabular data into a Markdown pipe table —
doc_pdf's extracted PDF tables and doc_text's CSV rows today, with room for a
future tabular format to reuse the same code rather than growing a third
near-identical copy.

Lives in its own module (not inside doc_pdf, the original owner) because
docs.py imports doc_pdf directly to populate CONVERTERS; a second converter
importing FROM doc_pdf would tie two independent format converters together
for no reason beyond "the code happened to be written there first". A small
neutral module both can depend on avoids that coupling and avoids a circular
import back through docs.py.
"""
from typing import List, Optional


class ParseFailed(Exception):
    """Raised by a converter ONLY around the exact call(s) into its
    underlying library's own open/parse step — never around the converter's
    own rendering logic (body walking, sheet/table formatting, budget
    tracking, ...). That boundary is the entire point of this class: a
    library exception (a corrupt/truncated file, a wrong password) and a
    genuine bug in OUR OWN code can raise the exact same Python exception
    TYPE (a stray `KeyError`/`AttributeError`/`ValueError` from a typo is
    exactly as likely as one from a real parser), so the only reliable way
    to tell them apart is WHERE the catch is scoped, not what type it
    catches. A converter that raises ParseFailed is asserting "this failure
    came from the library's own parse, not from me" — docs.py trusts that
    assertion and maps it to the shared {EncryptedDocument, UnreadableDocument}
    catalogue without needing to know each library's own exception types.

    `encrypted=True` means the converter positively identified the failure
    as password-protection (e.g. doc_office's OLE2 magic-byte sniff), not
    ordinary corruption — docs.py maps that to a different, more useful
    catalogue entry (EncryptedDocument) than the default (UnreadableDocument).
    """

    def __init__(self, message: str, *, encrypted: bool = False):
        super().__init__(message)
        self.encrypted = encrypted


def format_cell(value: Optional[str]) -> str:
    """Escape one cell's text for safe placement inside a Markdown pipe
    table: collapse embedded newlines (which would otherwise break the row
    across multiple lines) and escape a literal '|' (which would otherwise
    be read as a column separator, misaligning every following row)."""
    return (value or "").replace("\n", " ").strip().replace("|", "\\|")


def format_row(cells: List[str], width: int) -> str:
    """Render one row of ALREADY-ESCAPED cells, padded to `width` columns.

    Exposed separately from table_to_markdown so a caller that needs to
    truncate a large table at a row boundary (doc_text's CSV cap) can build
    and measure rows one at a time instead of rendering the whole table
    first and cutting it after the fact.
    """
    padded = list(cells) + [""] * (width - len(cells))
    return "| " + " | ".join(padded) + " |"


# The fixed opening every converter's self-truncation marker starts with —
# owned here, once, so "this converter already cut real content and said so"
# can be recognized apart from ordinary document text with a plain substring
# check instead of a regex that has to parse the free-form prose after it.
# That regex used to require NO parentheses between "*(" and the closing
# ")*" — reasonable until a converter's message legitimately needed a nested
# parenthetical aside (e.g. "...could not be determined (its XML has no
# declared dimension)"), at which point the regex silently stopped matching
# and the whole marker went undetected. Anchoring on this fixed,
# converter-agnostic PREFIX instead means a converter's own detail text
# after it can say anything — including its own nested parens — without
# ever being able to change what a substring check derives from it.
#
# docs.py no longer reads this constant back out to decide anything itself:
# `_converter_self_truncated`, the response-level check this constant used
# to feed, was deleted along with the response-level trim
# (docs.MAX_MARKDOWN_CHARS) it existed for — es_doc_extract returns a
# receipt now, not a trimmed excerpt, so there is nothing left to derive
# that boolean for. truncation_marker() below is the sole remaining
# reader/writer of this constant; a caller that wants to detect a marker
# in-band (as tests/test_reader.py does) does its own plain substring check
# against it directly.
TRUNCATION_SENTINEL = "*(truncated"


def truncation_marker(detail: str) -> str:
    """Build one converter's self-truncation marker: `detail` (the specific
    reason/counts, e.g. "after 12 of 30 events — the 30000-character limit
    was reached") is wrapped in the "*(truncated ...)*" convention every
    converter's marker follows, always starting with the shared
    TRUNCATION_SENTINEL so docs.py's detection never depends on parsing
    `detail`'s own text — `detail` is free to contain nested parentheses,
    get reworded, add more counts, etc. without ever breaking detection.

    Returns the marker text alone, NOT prefixed with "\\n\\n" — callers
    differ on how it's joined to the rest of their markdown (most append
    "\\n\\n" + this to their final string; doc_office's per-sheet note
    instead appends it as its own already-blank-line-separated list item),
    so joining is left to each call site rather than assumed here.
    """
    return f"{TRUNCATION_SENTINEL} {detail})*"


def rfind_safe_cut(text: str, limit: int) -> int:
    """Return a cut index <= min(limit, len(text)) that falls on a line
    boundary — the last newline at or before `limit` — so slicing `text` at
    the returned index can never land inside a single-line token such as a
    Markdown image link (`![page N](path)`, always emitted on one line by
    doc_pdf.convert). Falls back to `limit` itself when no newline exists
    before it (the whole span up to `limit` is one unbroken line) — a
    nicety when a line boundary is available, not a guarantee independent
    of the text's shape.

    Shared by mcp_server._cap_content (capping `es_read`'s returned content)
    and docs.extract()'s `preview` cut — both need "cut near a character
    budget without slicing a token in half", so one function owns the
    rfind rather than two independently-maintained copies of it.
    """
    limit = max(0, min(limit, len(text)))
    cut = text.rfind("\n", 0, limit)
    return cut if cut > 0 else limit


def table_to_markdown(table: List[List[Optional[str]]]) -> str:
    """Render one extracted table (a list of rows, each a list of cells) as
    a Markdown pipe table with a header separator. Returns "" for an
    empty/all-blank table — nothing meaningful to render."""
    rows = [[format_cell(c) for c in row] for row in table if row]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    head, *body = rows
    out = [format_row(head, width),
           "|" + "|".join([" --- "] * width) + "|"]
    out += [format_row(r, width) for r in body]
    return "\n".join(out)
