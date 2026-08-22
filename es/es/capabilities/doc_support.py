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
