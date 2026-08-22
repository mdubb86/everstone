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
