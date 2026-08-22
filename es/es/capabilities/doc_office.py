"""Word (.docx) and Excel (.xlsx) documents -> Markdown.

.docx: walked in DOCUMENT ORDER, not python-docx's separate `.paragraphs`/
`.tables` lists — those are two independent flat lists that do not preserve
how paragraphs and tables were actually interleaved in the source (a table
sitting between two paragraphs would come out AFTER both, since each list is
walked to completion on its own). The fix is to walk `document.element.body`
directly: the underlying WordprocessingML XML has one true order (`<w:p>` and
`<w:tbl>` children of `<w:body>`, in document order), and python-docx's
higher-level `Paragraph`/`Table` wrappers can be constructed straight from
those child elements. This module never asks `document.paragraphs` or
`document.tables` for order — only for nothing, since those attributes are
never read here at all.

Heading paragraph styles ("Heading 1".."Heading 6") map to `#`.."######"` so
the document's own outline becomes the `##`-boundary structure the later
paging reader depends on (see docs.py's CONVERTERS docstring); a non-heading
paragraph is emitted as plain text.

.xlsx: one `## <sheet name>` heading per worksheet — the natural (and only)
structural unit a spreadsheet has, which is exactly what makes a workbook
pageable by that same reader. Rows render as a pipe table via doc_support.

Formula cells are read as the FORMULA TEXT (`data_only=False`), never the
cached result (`data_only=True`). A workbook saved by any tool that never ran
Excel's calculation engine — including openpyxl itself, which is exactly the
shape of file a Telegram upload is likely to be — has NO cached value for a
formula cell at all: `data_only=True` against such a file silently renders
those cells as blank, not as "no cache available". The formula text is
always present and deterministic; a cache that only sometimes exists is not
a foundation this module can build predictable behavior on.

Both formats truncate at a whole-BLOCK boundary against a shared character
budget (MAX_CHARS below) — a whole paragraph/table for .docx, a whole row for
.xlsx — mirroring doc_text.MAX_CHARS / doc_ics.MAX_ICS_CHARS: docs.py's own
_truncate_markdown only knows PDF-style "## Page N" boundaries, so neither
format here can rely on it and each truncates itself before returning.
XLSX_MAX_ROWS/XLSX_MAX_COLS are a SEPARATE, structural cap on top of that
budget: a sheet's reported used range can be dramatically larger than its
real content (a single stray value at, say, ZZ10000 reports a 10000-row x
702-column used range for what is, in substance, two cells of data), and
that must be bounded before the character budget ever gets a chance to look
at it, or a single pathological sheet could force iterating tens of
thousands of all-blank rows for no reason.
"""
from pathlib import Path
from typing import List, Optional, Tuple

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook

from es.capabilities.doc_support import format_cell, format_row, table_to_markdown

# Shared by both formats — see module docstring.
MAX_CHARS = 30_000

# Per-sheet structural caps — see module docstring. Independent of MAX_CHARS.
XLSX_MAX_ROWS = 5000
XLSX_MAX_COLS = 256

_HEADING_LEVELS = {f"Heading {n}": n for n in range(1, 7)}

_W_P = qn("w:p")
_W_TBL = qn("w:tbl")


# --------------------------------------------------------------------------
# .docx
# --------------------------------------------------------------------------

def _heading_level(paragraph: Paragraph) -> Optional[int]:
    style = paragraph.style
    name = style.name if style is not None else None
    return _HEADING_LEVELS.get(name)


def _paragraph_block(paragraph: Paragraph) -> Optional[str]:
    text = paragraph.text.strip()
    if not text:
        return None
    level = _heading_level(paragraph)
    if level:
        return f"{'#' * level} {text}"
    return text


def _table_block(table: Table) -> Optional[str]:
    rows = [[cell.text for cell in row.cells] for row in table.rows]
    return table_to_markdown(rows) or None


def _iter_body_blocks(document: Document) -> List[str]:
    """Walk `document.element.body`'s direct children IN ORDER, mapping each
    `<w:p>` to a Paragraph and each `<w:tbl>` to a Table — see the module
    docstring for why this, and not `document.paragraphs`/`document.tables`,
    is the only way to keep the source's true interleaving. Anything else
    under `<w:body>` (e.g. the trailing `<w:sectPr>` section-properties
    element) is silently skipped — it carries no displayable content."""
    blocks: List[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == _W_P:
            block = _paragraph_block(Paragraph(child, document))
        elif child.tag == _W_TBL:
            block = _table_block(Table(child, document))
        else:
            continue
        if block:
            blocks.append(block)
    return blocks


def _convert_docx(source: Path) -> str:
    document = Document(str(source))
    blocks = _iter_body_blocks(document)
    if not blocks:
        return "*(this document has no readable text or tables)*"

    lines: List[str] = []
    used = 0
    kept = 0
    for block in blocks:
        cost = len(block) + (2 if kept > 0 else 0)  # blank line before it
        if kept > 0 and used + cost > MAX_CHARS:
            break
        if kept > 0:
            lines.append("")
        lines.append(block)
        used += cost
        kept += 1

    md = "\n".join(lines)
    if kept < len(blocks):
        remaining = len(blocks) - kept
        md += (f"\n\n*(truncated after {kept} of {len(blocks)} sections — the "
               f"{MAX_CHARS}-character limit was reached; this document has "
               "no page range to resume from, so ask for a narrower excerpt "
               f"if the remaining {remaining} section"
               f"{'s' if remaining != 1 else ''} are needed)*")
    return md


# --------------------------------------------------------------------------
# .xlsx
# --------------------------------------------------------------------------

def _render_sheet_rows(ws, budget: int) -> Tuple[str, int, int, int]:
    """Render up to XLSX_MAX_ROWS x XLSX_MAX_COLS of `ws` as one pipe table
    (first row as the header), stopping early if the running character
    `budget` is exhausted first. Returns (markdown, kept_rows, total_rows,
    capped_rows).

    total_rows == 0 is the signal for "this sheet has no data at all" —
    distinct from a genuinely empty sheet reporting `ws.max_row == 1`
    (openpyxl's default dimension for a sheet with zero cells ever assigned):
    iterating such a sheet yields NO rows at all, whereas a sheet with real
    (even sparse) content yields a row entry — all-blank cells included — for
    every row up to its true extent. `saw_any` below is what tells the two
    apart; trusting `ws.max_row` alone would misreport the empty case as "1
    of 1 rows truncated".
    """
    total_rows = ws.max_row or 0
    total_cols = ws.max_column or 0
    if total_rows == 0 or total_cols == 0:
        return "", 0, 0, 0
    capped_cols = min(total_cols, XLSX_MAX_COLS)
    capped_rows = min(total_rows, XLSX_MAX_ROWS)

    lines: List[str] = []
    used = 0
    kept = 0
    saw_any = False
    for row in ws.iter_rows(min_row=1, max_row=capped_rows,
                             min_col=1, max_col=capped_cols,
                             values_only=True):
        saw_any = True
        cells = [format_cell("" if v is None else str(v)) for v in row]
        pieces = [format_row(cells, capped_cols)]
        if kept == 0:
            pieces.append("|" + "|".join([" --- "] * capped_cols) + "|")
        cost = sum(len(p) + 1 for p in pieces)
        if kept > 0 and used + cost > budget:
            break
        lines.extend(pieces)
        used += cost
        kept += 1

    if not saw_any:
        return "", 0, 0, 0
    return "\n".join(lines), kept, total_rows, capped_rows


def _sheet_truncation_note(kept: int, capped_rows: int, total_rows: int) -> Optional[str]:
    if total_rows == 0:
        return None  # genuinely empty sheet — nothing was truncated
    if kept >= capped_rows and capped_rows == total_rows:
        return None  # every real row was rendered — nothing to note
    if kept < capped_rows:
        # the character budget cut this sheet short before even its
        # structural row cap was reached
        return (f"truncated after {kept} of {capped_rows} rows shown — the "
                "character limit for this document was reached")
    return (f"truncated after {capped_rows} of {total_rows} rows — this "
            f"sheet exceeds the {XLSX_MAX_ROWS}-row-per-sheet limit")


def _convert_xlsx(source: Path) -> str:
    # read_only mode streams rows lazily straight out of the underlying zip
    # archive — the workbook (and archive) must stay open for the ENTIRE
    # sheet-by-sheet render loop below, not just long enough to list
    # `worksheets`, or the first `iter_rows` call on any sheet raises
    # "Attempt to use ZIP archive that was already closed".
    wb = load_workbook(str(source), data_only=False, read_only=True)
    try:
        worksheets = list(wb.worksheets)
        if not worksheets:
            return "*(this workbook has no sheets)*"

        lines: List[str] = []
        used = 0
        sheets_rendered = 0
        for ws in worksheets:
            header = f"## {ws.title}"
            header_cost = len(header) + (2 if lines else 0)
            if lines and used + header_cost > MAX_CHARS:
                break  # no budget left even for this sheet's heading
            if lines:
                lines.append("")
            lines.append(header)
            used += header_cost
            sheets_rendered += 1

            table_md, kept, total_rows, capped_rows = _render_sheet_rows(ws, MAX_CHARS - used)
            if table_md:
                lines.append("")
                lines.append(table_md)
                used += len(table_md) + 2

            note = _sheet_truncation_note(kept, capped_rows, total_rows)
            if note:
                lines.append("")
                marker = f"*({note} — this file has no page range to resume " \
                          "from, so ask for a narrower export if the rest is " \
                          "needed)*"
                lines.append(marker)
                used += len(marker) + 2
                # A per-sheet character-budget cut means the shared budget is
                # spent — no point attempting further sheets.
                if used >= MAX_CHARS:
                    break
    finally:
        wb.close()

    md = "\n".join(lines)
    if sheets_rendered < len(worksheets):
        remaining = len(worksheets) - sheets_rendered
        md += (f"\n\n*(truncated after {sheets_rendered} of {len(worksheets)} "
               f"sheets — the {MAX_CHARS}-character limit was reached; this "
               "workbook has no page range to resume from, so ask for a "
               f"narrower export if the remaining {remaining} sheet"
               f"{'s' if remaining != 1 else ''} are needed)*")
    return md


_HANDLERS = {
    ".docx": _convert_docx,
    ".xlsx": _convert_xlsx,
}


def convert(source: Path, adir: Path,
            pages: Optional[List[int]] = None, **_ignored) -> Tuple[str, List[Path]]:
    """Return (markdown, []) — see the module docstring's images note:
    embedded .docx images are not extracted here (tracked as a follow-up),
    so this always returns an empty image list, same as doc_text/doc_ics.

    `pages`/`adir` are accepted for signature parity with every other
    converter (docs.extract calls all of them uniformly) but unused: neither
    format implements `page_count`/`render`, so docs.py never lets an
    explicit `pages` argument reach here.
    """
    handler = _HANDLERS[source.suffix.lower()]
    return handler(source), []
