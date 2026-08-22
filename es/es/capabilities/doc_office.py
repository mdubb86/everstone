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
thousands of all-blank rows for no reason. DOCX_MAX_TABLE_ROWS is the .docx
analogue: it bounds the cost of rendering a SINGLE table block, independent
of MAX_CHARS, for the same reason (see `_table_block`).

.docx conversion is deliberately LAZY end to end (`_iter_body_blocks` is a
generator; `_convert_docx` stops pulling from it the moment MAX_CHARS is
spent) rather than "collect every block, then truncate": this was a verified
unbounded-cost bug, not a style preference. Measured before the fix, walking
`document.element.body` fully and formatting every block before truncating
cost ~0.4ms/paragraph — 3.7s at 10,000 paragraphs, 111.65s at 300,000 (a
plain 0.84 MB .docx, well inside MAX_DOCUMENT_BYTES) — because
`Paragraph.style` does a linear style-collection lookup on every call, and
that cost was paid for every block in the document even though only a small
prefix of the OUTPUT ever survives the character budget. After the fix the
same 300,000-paragraph document converts in well under a second: cost is
O(kept blocks), not O(document size). A `.docx` is a zip, so
MAX_DOCUMENT_BYTES only bounds the COMPRESSED size (a zip bomb can inflate
far past it) — this lazy walk plus the per-table row cap below are the real
defence, not the byte ceiling.

.xlsx: `ws.max_row`/`ws.max_column` in `read_only` mode come from the sheet
XML's `<dimension>` element — and are `None`, not `0`, when that element is
absent. This is not exotic: `openpyxl.Workbook(write_only=True)` (a
mainstream way tools generate large spreadsheets — pandas/ETL exports
included) never writes `<dimension>` at all. A prior version of this module
read `total_rows = ws.max_row or 0`, so `None or 0` produced `0`, which was
then read as "genuinely empty sheet" and short-circuited before a single row
was ever iterated — a verified DATA-LOSS bug: a 300,000-row write_only
workbook converted to a bare `"## Sheet"` heading with no error, no
truncation marker, and `{ok: true}`. `_render_sheet_rows` below never trusts
`ws.max_row`/`ws.max_column` as a presence signal any more — only actually
iterating and observing a row (`saw_any`) proves a sheet has content, dimension
or no dimension. `ws.calculate_dimension(force=True)` was considered as a fix
and measured at ~7.9s / a full read-only pass over a 300,000-row sheet (it
walks every row to find the true extent) — paying that cost just to print an
accurate row count would reintroduce the same O(sheet size) cost this module
otherwise avoids by rendering read_only rows lazily, so it is deliberately
never called here. When the sheet's true dimension is unknown, this module
reports what it can prove (rows actually rendered, and whether more exist
past the structural cap) without fabricating an exact total.
"""
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from es.capabilities.doc_support import ParseFailed, format_cell, format_row

# Shared by both formats — see module docstring.
MAX_CHARS = 30_000

# --------------------------------------------------------------------------
# Parse-step error mapping (see doc_support.ParseFailed for the boundary
# rule this exists to enforce).
# --------------------------------------------------------------------------
#
# python-docx and openpyxl each raise their OWN exception type for a parse
# failure — PackageNotFoundError (python-docx, which itself normalizes a
# bad/missing zip into this one type), BadZipFile (openpyxl, which does NOT
# normalize it), InvalidFileException (openpyxl's own filename-extension
# guard — included for defense in depth even though docs.py's dispatch
# already only ever routes a real .xlsx path here, so it should never
# actually fire in practice), OSError (python-docx's own "no valid workbook/
# document part" case), ParseError (openpyxl's read_only streaming reader
# uses the stdlib xml.etree parser regardless of lxml being installed),
# XMLSyntaxError (python-docx uses lxml), KeyError (a valid zip missing
# "[Content_Types].xml" entirely — both libraries do a plain dict-style
# archive lookup), and AttributeError (a valid zip whose
# "[Content_Types].xml" parses but has no default namespace, so python-docx's
# lxml class lookup never upgrades it to its own CT_Types wrapper, and the
# next attribute access on it fails). All nine are verified empirically (not
# guessed) against real python-docx/openpyxl behavior for every ordinary
# "wrong/damaged file" shape (a renamed extension, a partial download) — see
# tests/test_docs.py's realistic-malformed-documents case.
#
# This tuple is used ONLY around the two open/parse call sites below
# (_open_docx, _open_xlsx, and the lazy per-row XML parse in
# _safe_row_iter) — never around this module's own rendering logic (body
# walking, sheet/table formatting, budget tracking). A bug in THAT code
# that happens to raise one of these same ordinary types (a typo'd dict
# key, an attribute on a None) must surface as itself, not get relabeled
# "corrupt file" — see doc_support.ParseFailed's docstring for why the
# BOUNDARY, not the exception type, is what keeps the two apart.
_PARSE_ERRORS = (PackageNotFoundError, InvalidFileException, BadZipFile,
                 OSError, ValueError, ParseError, XMLSyntaxError,
                 KeyError, AttributeError)

# The OLE2/CFBF container signature (MS-CFB) — every legitimate, unencrypted
# .docx/.xlsx is a zip archive; a password-protected one is instead stored in
# this legacy container format (the same one .doc/.xls used), which is how
# real Office password-protection actually works, not a guess. Neither
# python-docx nor openpyxl exposes a distinct "needs a password" exception —
# both simply fail to open the file as a zip, indistinguishable by exception
# type alone from ordinary corruption (verified empirically against both
# libraries) — so this sniffs the file's own magic bytes instead.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _is_ole2_container(source: Path) -> bool:
    try:
        with open(source, "rb") as f:
            return f.read(len(_OLE2_MAGIC)) == _OLE2_MAGIC
    except OSError:
        return False


def _raise_parse_failed(source: Path, exc: Exception) -> None:
    """Always raises ParseFailed. Shared by both formats: neither
    python-docx nor openpyxl distinguishes "needs a password" from ordinary
    corruption by exception type, so both route through the same OLE2 sniff
    (see _is_ole2_container above)."""
    if _is_ole2_container(source):
        raise ParseFailed(
            f"{source.name} is password-protected — es cannot open "
            "encrypted Word/Excel documents; ask the user for an unlocked "
            "copy", encrypted=True) from exc
    raise ParseFailed(
        f"{source.name} could not be read as a Word/Excel document — it "
        "may be corrupt, truncated, or not actually a .docx/.xlsx file; "
        "ask the user to resend it") from exc


def _open_docx(source: Path) -> Document:
    """The ONLY place .docx parsing can fail: verified empirically that
    python-docx's `Document()` eagerly parses the entire package (zip +
    every XML part it needs) at open time — unlike openpyxl's read_only
    mode (see _open_xlsx/_safe_row_iter below), nothing about the walk in
    _convert_docx re-enters the library's own parser."""
    try:
        return Document(str(source))
    except _PARSE_ERRORS as e:
        _raise_parse_failed(source, e)


def _open_xlsx(source: Path):
    """Opens the workbook-level parts (workbook.xml, styles, shared
    strings) eagerly — but NOT a sheet's own XML, which read_only mode
    streams lazily (see _safe_row_iter: a truncated sheet1.xml only raises
    while actually ITERATING that sheet's rows, verified empirically, never
    here)."""
    try:
        return load_workbook(str(source), data_only=False, read_only=True)
    except _PARSE_ERRORS as e:
        _raise_parse_failed(source, e)


def _safe_row_iter(row_iter, source: Path):
    """Wrap ONLY the underlying iterator's own `next()` call — the exact
    point where openpyxl's read_only reader lazily parses the next chunk of
    a sheet's XML (verified empirically: a truncated sheet1.xml raises
    `xml.etree.ElementTree.ParseError` mid-iteration, not at `load_workbook()`
    time — read_only mode defers a SHEET's own parse to iteration even
    though the workbook-level parts are already parsed by then). Every
    caller's own row-processing logic (format_cell/format_row, budget
    tracking, `saw_any`/`kept` bookkeeping) stays entirely outside this
    function and outside its try block, reached only via the values this
    generator yields — so a bug in that logic can never be caught here and
    relabeled "corrupt file"."""
    while True:
        try:
            row = next(row_iter)
        except StopIteration:
            return
        except _PARSE_ERRORS as e:
            _raise_parse_failed(source, e)
        yield row

# Per-sheet structural caps — see module docstring. Independent of MAX_CHARS.
XLSX_MAX_ROWS = 5000
XLSX_MAX_COLS = 256

# Per-table structural cap for .docx — see module docstring. A single
# pathological table (e.g. a 20,000-row spreadsheet paste) would otherwise be
# rendered in full as ONE block before the character budget ever gets a
# chance to reject it wholesale; this bounds that cost independent of
# MAX_CHARS, mirroring XLSX_MAX_ROWS.
DOCX_MAX_TABLE_ROWS = 2000

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


# Reserved headroom subtracted from the budget handed to `_table_block`, so
# that a table's own truncation marker (appended after its internal budget
# check) can never itself push the block's total length past what the
# OUTER loop in `_convert_docx` is willing to accept — see `_table_block`.
_TABLE_MARKER_RESERVE = 300


def _table_block(table: Table, budget: int) -> Optional[str]:
    """Render `table` as a pipe table, bounded by BOTH DOCX_MAX_TABLE_ROWS
    (a structural per-table row cap) AND `budget` (the character budget
    still available in the document at the point this table was reached) —
    mirroring `_render_sheet_rows`'s dual cap for the same reason: a single
    pathological table must never cost O(its own size) before the
    document-level budget ever gets a chance to reject it wholesale.
    Measured before this cap existed: a single 20,000x6 table rendered
    whole, unconditionally, in ~4.4s / +52MB RSS — regardless of MAX_CHARS.

    Built incrementally with `format_cell`/`format_row` (not
    `table_to_markdown`, which needs the WHOLE table materialized up front
    to compute one shared column width) so this function can stop the
    instant either cap is hit, without ever having extracted text from a
    row beyond that point — `row.cells` is what actually walks a row's XML,
    so skipping it (not just discarding its output) is what keeps this
    bounded. Column width is taken from the table's own FIRST row and
    applied to every row after: a python-docx `Table` is rectangular by
    construction (row-level cell merging aside), so this is exact — unlike
    the analogous first-row-width guess this module makes for a
    dimension-less .xlsx sheet, which is a heuristic because a spreadsheet
    has no such guarantee.
    """
    lines: List[str] = []
    used = 0
    kept = 0
    width: Optional[int] = None
    row_cap_hit = False
    budget_hit = False
    for i, row in enumerate(table.rows):
        if i >= DOCX_MAX_TABLE_ROWS:
            row_cap_hit = True
            break
        cells = [format_cell(cell.text) for cell in row.cells]
        if width is None:
            width = len(cells)
        pieces = [format_row(cells, width)]
        if kept == 0:
            pieces.append("|" + "|".join([" --- "] * width) + "|")
        cost = sum(len(p) + 1 for p in pieces)
        if kept > 0 and used + cost > budget:
            budget_hit = True
            break
        lines.extend(pieces)
        used += cost
        kept += 1

    if not lines:
        return None

    md = "\n".join(lines)
    if row_cap_hit:
        md += (f"\n\n*(table truncated after {kept} rows — this table "
               f"exceeds the {DOCX_MAX_TABLE_ROWS}-row-per-table limit)*")
    elif budget_hit:
        md += (f"\n\n*(table truncated after {kept} rows — the character "
               "limit for this document was reached)*")
    return md


def _iter_body_blocks(document: Document, remaining_budget) -> Iterator[str]:
    """Yield blocks from `document.element.body`'s direct children IN ORDER,
    LAZILY — mapping each `<w:p>` to a Paragraph and each `<w:tbl>` to a
    Table — see the module docstring for why walking the body directly (and
    not `document.paragraphs`/`document.tables`) is the only way to keep the
    source's true interleaving. Anything else under `<w:body>` (e.g. the
    trailing `<w:sectPr>` section-properties element) is silently skipped —
    it carries no displayable content.

    This is a generator, not a list, on purpose: `_paragraph_block` (via
    `Paragraph.style`) and `_table_block` are the expensive steps in this
    module — see the module docstring's measured numbers — so `_convert_docx`
    must be able to stop asking this generator for more the moment its
    character budget is spent, without this function having formatted a
    single block beyond that point. `document.element.body.iterchildren()`
    is itself already a lazy walk over the (already-parsed-in-memory) XML
    tree, so nothing upstream of this loop is re-done by stopping early.

    `remaining_budget` is a zero-arg callable read fresh each time a
    `<w:tbl>` is reached (not a snapshot taken once up front), so a table's
    own internal cap always sees how much of MAX_CHARS is actually left at
    that point in the walk, not how much was left when iteration started.
    """
    for child in document.element.body.iterchildren():
        if child.tag == _W_P:
            block = _paragraph_block(Paragraph(child, document))
        elif child.tag == _W_TBL:
            budget = max(0, remaining_budget() - _TABLE_MARKER_RESERVE)
            block = _table_block(Table(child, document), budget)
        else:
            continue
        if block:
            yield block


def _convert_docx(source: Path) -> str:
    document = _open_docx(source)

    lines: List[str] = []
    used = 0
    kept = 0
    truncated = False

    def remaining_budget() -> int:
        return MAX_CHARS - used

    for block in _iter_body_blocks(document, remaining_budget):
        cost = len(block) + (2 if kept > 0 else 0)  # blank line before it
        if kept > 0 and used + cost > MAX_CHARS:
            truncated = True
            break
        if kept > 0:
            lines.append("")
        lines.append(block)
        used += cost
        kept += 1

    if kept == 0:
        return "*(this document has no readable text or tables)*"

    md = "\n".join(lines)
    if truncated:
        # The true total section count is deliberately NOT reported here:
        # knowing it would require walking the rest of the document after
        # all, which is exactly the O(document size) cost this function
        # exists to avoid (see the module docstring).
        md += (f"\n\n*(truncated after {kept} section"
               f"{'s' if kept != 1 else ''} — the {MAX_CHARS}-character "
               "limit was reached; this document has no page range to "
               "resume from, so ask for a narrower excerpt if more is "
               "needed)*")
    return md


# --------------------------------------------------------------------------
# .xlsx
# --------------------------------------------------------------------------

def _render_sheet_rows(ws, budget: int, source: Path) -> Tuple[str, int, Optional[int], int]:
    """Render up to XLSX_MAX_ROWS x XLSX_MAX_COLS of `ws` as one pipe table
    (first row as the header), stopping early if the running character
    `budget` is exhausted first. Returns (markdown, kept_rows, total_rows,
    capped_rows).

    `ws.max_row`/`ws.max_column` are `None` (not `0`) when the sheet's XML
    has no `<dimension>` element at all — e.g. every sheet written by
    `openpyxl.Workbook(write_only=True)`. This function NEVER treats that
    metadata as proof of anything: only actually iterating and observing a
    row (`saw_any` below) proves the sheet has content. A sheet that DOES
    declare a dimension but has zero real cells (openpyxl's default
    `max_row == max_col == 1` for a brand new empty sheet) still yields no
    rows when iterated — `saw_any` is what tells that case apart from "one
    row of real, if blank, content" too.

    `total_rows` in the return value is `None` when the sheet's true row
    count could not be determined without a full scan (dimension unknown,
    and iteration was cut short by either the structural row cap or the
    character budget before reaching the sheet's actual end) — see the
    module docstring for why this function deliberately never calls
    `ws.calculate_dimension(force=True)` to resolve that. Callers must
    handle `total_rows is None` rather than assume an int.
    """
    known_rows = ws.max_row
    known_cols = ws.max_column
    # Fall back to the row structural cap itself as the iteration bound when
    # the dimension is unknown (or, degenerately, reported as 0) — this
    # keeps the read_only parser's own early-stop (it never reads past
    # max_row) doing the bounding, instead of this function ever attempting
    # to iterate an unbounded sheet.
    row_cap = min(known_rows, XLSX_MAX_ROWS) if known_rows else XLSX_MAX_ROWS
    if known_cols:
        capped_cols = min(known_cols, XLSX_MAX_COLS)
    else:
        # Column count unknown too. Forcing every row to XLSX_MAX_COLS wide
        # (256) here would be its own budget-exhausting bug: every row,
        # including ones with two real cells, would be padded out to 256
        # pipe-table columns, so the FIRST such row alone could burn most of
        # the character budget and starve every row after it — that's not
        # hypothetical, it's what happened before this fallback existed.
        # Peeking at row 1's own actual width is a light, bounded probe (one
        # single-row read, not a full-sheet scan) and is representative for
        # the common case this guards against (a write_only/no-<dimension>
        # workbook whose rows are written with a consistent shape, e.g. via
        # repeated `ws.append([...])` calls). A later row genuinely wider
        # than row 1 will have its extra columns dropped, same as any sheet
        # whose real width exceeds XLSX_MAX_COLS today.
        first_row = next(_safe_row_iter(
            ws.iter_rows(min_row=1, max_row=1, values_only=True), source), None)
        capped_cols = min(len(first_row), XLSX_MAX_COLS) if first_row else XLSX_MAX_COLS

    lines: List[str] = []
    used = 0
    kept = 0
    saw_any = False
    idx = 0
    stopped_on_budget = False
    # Ask for one row PAST row_cap purely to detect "does more content exist
    # beyond the structural cap" — cheap, because the read_only parser stops
    # as soon as it sees a row index past this bound, so this never costs a
    # full-sheet scan even when the dimension is unknown.
    #
    # Wrapped through _safe_row_iter, not iterated directly: read_only mode
    # parses a sheet's own XML lazily, one row at a time, so a truncated/
    # malformed sheet1.xml raises HERE, mid-iteration — never at
    # load_workbook() time (see _open_xlsx/_safe_row_iter's docstrings).
    for idx, row in enumerate(_safe_row_iter(
            ws.iter_rows(min_row=1, max_row=row_cap + 1,
                         min_col=1, max_col=capped_cols,
                         values_only=True), source), start=1):
        saw_any = True
        if idx > row_cap:
            break
        cells = [format_cell("" if v is None else str(v)) for v in row]
        pieces = [format_row(cells, capped_cols)]
        if kept == 0:
            pieces.append("|" + "|".join([" --- "] * capped_cols) + "|")
        cost = sum(len(p) + 1 for p in pieces)
        if kept > 0 and used + cost > budget:
            stopped_on_budget = True
            break
        lines.extend(pieces)
        used += cost
        kept += 1

    if not saw_any:
        return "", 0, 0, 0

    if known_rows is not None:
        total_rows: Optional[int] = known_rows
    elif stopped_on_budget:
        total_rows = None  # cut short by the budget; true extent still unknown
    elif idx > row_cap:
        total_rows = None  # more than row_cap rows exist; exact count unknown
    else:
        total_rows = idx  # generator ran to completion at/under the cap — that IS the true count

    return "\n".join(lines), kept, total_rows, row_cap


def _sheet_truncation_note(kept: int, capped_rows: int,
                            total_rows: Optional[int]) -> Optional[str]:
    if total_rows == 0:
        return None  # genuinely empty sheet — nothing was truncated
    if total_rows is not None and kept >= capped_rows and capped_rows == total_rows:
        return None  # every real row was rendered — nothing to note
    if kept < capped_rows:
        # the character budget cut this sheet short before even its
        # structural row cap was reached
        if total_rows is not None and total_rows > capped_rows:
            # both limits are in play — say so, rather than reporting
            # `capped_rows` as if it were the sheet's real size (that hid
            # the true row count behind the structural cap's denominator).
            return (f"truncated after {kept} of {total_rows} rows — the "
                    "character limit for this document was reached first; "
                    f"this sheet also exceeds the {XLSX_MAX_ROWS}-row-per-"
                    "sheet limit")
        if total_rows is None:
            return (f"truncated after {kept} rows shown — the character "
                    "limit for this document was reached; this sheet's "
                    "total row count could not be determined (its XML has "
                    "no declared dimension)")
        return (f"truncated after {kept} of {capped_rows} rows shown — the "
                "character limit for this document was reached")
    if total_rows is None:
        return (f"truncated after {capped_rows} rows — this sheet exceeds "
                f"the {XLSX_MAX_ROWS}-row-per-sheet limit (its exact total "
                "is unknown: this sheet's XML has no declared dimension)")
    return (f"truncated after {capped_rows} of {total_rows} rows — this "
            f"sheet exceeds the {XLSX_MAX_ROWS}-row-per-sheet limit")


def _convert_xlsx(source: Path) -> str:
    # read_only mode streams rows lazily straight out of the underlying zip
    # archive — the workbook (and archive) must stay open for the ENTIRE
    # sheet-by-sheet render loop below, not just long enough to list
    # `worksheets`, or the first `iter_rows` call on any sheet raises
    # "Attempt to use ZIP archive that was already closed".
    wb = _open_xlsx(source)
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

            table_md, kept, total_rows, capped_rows = _render_sheet_rows(
                ws, MAX_CHARS - used, source)
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
