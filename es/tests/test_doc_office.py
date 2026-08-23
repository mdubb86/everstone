import re
import time
import zipfile

import pytest

from es.capabilities import doc_office


def test_docx_headings_become_markdown_headings(docx_file, tmp_path):
    md, images = doc_office.convert(docx_file, tmp_path)
    assert "# Season Overview" in md
    assert "## Fees" in md
    assert "Practices are Tuesdays" in md
    assert images == []


def test_docx_tables_render_as_pipe_tables(docx_file, tmp_path):
    md, _ = doc_office.convert(docx_file, tmp_path)
    assert "| Item | Cost |" in md
    assert "| Kit | $65 |" in md


def test_docx_preserves_document_order(tmp_path):
    """python-docx exposes paragraphs and tables as SEPARATE lists that do not
    preserve their interleaved order. A table between two paragraphs must come
    out between them, not appended at the end."""
    from docx import Document
    p = tmp_path / "ordered.docx"
    d = Document()
    d.add_paragraph("FIRST paragraph")
    t = d.add_table(rows=1, cols=1)
    t.cell(0, 0).text = "MIDDLE cell"
    d.add_paragraph("LAST paragraph")
    d.save(str(p))
    md, _ = doc_office.convert(p, tmp_path)
    assert md.index("FIRST") < md.index("MIDDLE") < md.index("LAST")


def test_xlsx_each_sheet_is_a_section(xlsx_file, tmp_path):
    """Sheets are the natural heading unit — this is what makes a workbook
    pageable by the reader."""
    md, _ = doc_office.convert(xlsx_file, tmp_path)
    assert "## Roster" in md
    assert "## Fees" in md


def test_xlsx_rows_render_as_a_table(xlsx_file, tmp_path):
    md, _ = doc_office.convert(xlsx_file, tmp_path)
    assert "| Name | Number |" in md
    assert "| Alice | 9 |" in md


def test_xlsx_large_sheet_truncates_with_a_marker(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "big.xlsx"
    wb = Workbook(); ws = wb.active
    for i in range(8000):
        ws.append([f"row{i}", i, "padding value here"])
    wb.save(str(p))
    md, _ = doc_office.convert(p, tmp_path)
    assert "truncated" in md.lower()


def test_xlsx_later_sheets_reachable_when_first_sheet_exhausts_budget(tmp_path):
    """Regression: before the per-sheet budget fix, a first sheet alone big
    enough to spend the whole MAX_CHARS budget made every LATER sheet vanish
    entirely -- not truncated, not noted, simply absent from both the
    Markdown and (since es_read's outline is built from `## ` headings) the
    outline itself. No parameter on es_doc_extract/es_read could reach a
    "## Summary"/"## Notes" sheet that was never emitted in the first place.
    Every sheet must now get at least a heading, and (per
    _render_sheet_rows's own first-row-always-shown rule) at least one row
    of real, independently-readable content."""
    from openpyxl import Workbook
    from es.capabilities import read as read_cap

    p = tmp_path / "report.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    for i in range(8000):
        ws.append([f"row{i}", i, "padding value here to burn the budget"])
    summary = wb.create_sheet("Summary")
    summary.append(["Total", 8000])
    notes = wb.create_sheet("Notes")
    notes.append(["Remember to reconcile next quarter"])
    wb.save(str(p))

    md, _ = doc_office.convert(p, tmp_path)

    # All three sheets are discoverable from the outline built over this one
    # markdown string -- exactly what es_read hands the agent on its FIRST,
    # argument-less call.
    outline = read_cap.outline(md)
    assert [s["title"] for s in outline] == ["Data", "Summary", "Notes"]

    # And each later sheet is independently readable, not merely a bare
    # heading with nothing behind it.
    summary_id = next(s["id"] for s in outline if s["title"] == "Summary")
    notes_id = next(s["id"] for s in outline if s["title"] == "Notes")
    assert "Total" in read_cap.section(md, summary_id)
    assert "reconcile" in read_cap.section(md, notes_id)


def test_xlsx_empty_workbook_does_not_raise(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "empty.xlsx"
    Workbook().save(str(p))
    md, _ = doc_office.convert(p, tmp_path)
    assert isinstance(md, str)


def test_docx_empty_document_does_not_raise(tmp_path):
    from docx import Document
    p = tmp_path / "empty.docx"
    Document().save(str(p))
    md, _ = doc_office.convert(p, tmp_path)
    assert isinstance(md, str)


def test_corrupt_office_file_raises(tmp_path):
    p = tmp_path / "bad.docx"
    p.write_bytes(b"not a zip at all")
    with pytest.raises(Exception):
        doc_office.convert(p, tmp_path)


def test_xlsx_formula_cells_render_their_value_or_formula_predictably(tmp_path):
    """openpyxl returns either the formula string or the cached value depending
    on data_only. Whichever you choose, be deliberate and consistent."""
    from openpyxl import Workbook
    p = tmp_path / "calc.xlsx"
    wb = Workbook(); ws = wb.active
    ws.append(["a", 2]); ws.append(["b", 3]); ws["B3"] = "=SUM(B1:B2)"
    wb.save(str(p))
    md, _ = doc_office.convert(p, tmp_path)
    assert isinstance(md, str) and "|" in md


# --------------------------------------------------------------------------
# Regression: verified DATA-LOSS bug — a write_only (or otherwise
# <dimension>-less) .xlsx sheet used to report `ws.max_row or 0` == 0 and
# get silently treated as "empty", discarding every row with no error and
# no truncation marker. See the doc_office module docstring for the full
# writeup and the measured ws.calculate_dimension(force=True) cost that
# ruled out the naive fix.
# --------------------------------------------------------------------------

def test_xlsx_write_only_workbook_rows_are_not_lost(tmp_path):
    """openpyxl.Workbook(write_only=True) never writes a <dimension> element,
    so ws.max_row/ws.max_column are None, not 0 — a mainstream way for tools
    (pandas/ETL exports included) to produce large spreadsheets, not an
    exotic edge case."""
    from openpyxl import Workbook
    p = tmp_path / "write_only.xlsx"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Data")
    for i in range(50):
        ws.append([f"row{i}", i])
    wb.save(str(p))

    md, _ = doc_office.convert(p, tmp_path)
    assert "## Data" in md
    assert "| row0 | 0 |" in md
    assert "| row49 | 49 |" in md
    # must not be mistaken for the genuinely-empty-sheet case
    assert "no data" not in md.lower()
    # nor mistaken for a truncated one — see
    # test_xlsx_small_write_only_sheet_is_not_falsely_reported_truncated for
    # the regression this guards (a fallback ceiling used in place of the
    # sheet's unknown true size used to look exactly like a budget cut).
    assert "truncated" not in md.lower()


def test_xlsx_small_write_only_sheet_is_not_falsely_reported_truncated(tmp_path):
    """Regression guard: `_sheet_truncation_note` used to infer "was this
    sheet cut short?" by comparing `kept` against a returned "capped_rows"
    value that meant "min(real total, XLSX_MAX_ROWS)" when the sheet's
    dimension was known, but just the bare XLSX_MAX_ROWS fallback ceiling
    (a safety bound for iteration, not a real count) when it was not — e.g.
    every `openpyxl.Workbook(write_only=True)` sheet. A tiny write_only
    sheet (2 real rows, well under both the character budget and
    XLSX_MAX_ROWS) satisfied `kept < capped_rows` (2 < 5000) purely because
    of that fallback value, and was reported as "truncated after 2 of 5000
    rows" even though nothing was cut at all. `_render_sheet_rows` now
    returns the actual reason (`hit_row_cap`/`hit_budget`) directly instead
    of leaving the caller to reconstruct it from an overloaded count."""
    from openpyxl import Workbook

    p = tmp_path / "tiny_write_only.xlsx"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Data")
    ws.append(["a", "b"])
    ws.append(["c", "d"])
    wb.save(str(p))

    md, _ = doc_office.convert(p, tmp_path)
    assert "| a | b |" in md
    assert "| c | d |" in md
    assert "truncated" not in md.lower()


def test_xlsx_dimension_stripped_sheet_rows_are_not_lost(tmp_path):
    """A sheet whose XML never had (or lost) its <dimension> element must
    render identically to one that has it. Build a normal workbook, then
    strip the <dimension .../> element directly from the zip member — this
    reproduces the bug report's exact repro without depending on how
    openpyxl happens to write write_only files today."""
    from openpyxl import Workbook
    p = tmp_path / "normal.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "Sheet1"
    for i in range(50):
        ws.append([f"r{i}", i])
    wb.save(str(p))

    with zipfile.ZipFile(str(p), "r") as zin:
        members = {n: zin.read(n) for n in zin.namelist()}
    sheet_name = next(n for n in members if n.startswith("xl/worksheets/sheet"))
    xml = members[sheet_name].decode("utf-8")
    assert "<dimension" in xml  # sanity: openpyxl did write one to strip
    members[sheet_name] = re.sub(r"<dimension[^/]*/>", "", xml).encode("utf-8")

    stripped = tmp_path / "stripped.xlsx"
    with zipfile.ZipFile(str(stripped), "w") as zout:
        for name, content in members.items():
            zout.writestr(name, content)

    md, _ = doc_office.convert(stripped, tmp_path)
    assert "| r0 | 0 |" in md
    assert "| r49 | 49 |" in md


def test_xlsx_large_write_only_sheet_converts_quickly_and_truncates(tmp_path):
    """The bug report's own repro: a 300,000-row write_only workbook. Must
    convert fast (bounded by the character budget, not the sheet size) and
    say so, instead of silently returning an empty/near-empty sheet."""
    from openpyxl import Workbook
    p = tmp_path / "big_write_only.xlsx"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Sheet")
    for i in range(300_000):
        ws.append([f"row{i}", i, "padding value here"])
    wb.save(str(p))

    t0 = time.time()
    md, _ = doc_office.convert(p, tmp_path)
    elapsed = time.time() - t0

    assert "| row0 |" in md
    assert "truncated" in md.lower()
    # Not a tight perf bound: opening/parsing a 300,000-row read_only
    # workbook (shared strings etc.) costs a few seconds regardless of this
    # module's own logic. The point of this test is that it completes at
    # all with the real data intact — see
    # test_docx_huge_paragraph_count_converts_quickly_and_bounded for the
    # tight sub-second bound (that cost lives entirely in this module, so
    # the fix can be held to it).
    assert elapsed < 30.0, f"conversion took {elapsed:.2f}s — should not scale with sheet size"


# --------------------------------------------------------------------------
# Item 3: truncation must report the sheet's TRUE row count, not the
# min(total_rows, XLSX_MAX_ROWS) structural cap masquerading as the total.
# --------------------------------------------------------------------------

def test_xlsx_truncation_note_reports_true_total_not_the_structural_cap(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "wide.xlsx"
    wb = Workbook(); ws = wb.active
    for i in range(8000):
        ws.append([f"row{i}", i, "padding value here"])
    wb.save(str(p))

    md, _ = doc_office.convert(p, tmp_path)
    assert "of 8000 rows" in md  # the real total ...
    assert "of 5000 rows" not in md  # ... not the row-per-sheet cap


# --------------------------------------------------------------------------
# Item 4: the row/column structural caps, exercised on their own — narrow
# rows so the character budget never fires first and can't mask a broken
# cap. monkeypatch keeps this independent of the module's real constants.
# --------------------------------------------------------------------------

def test_xlsx_row_cap_truncates_before_the_character_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_office, "XLSX_MAX_ROWS", 10)
    from openpyxl import Workbook
    p = tmp_path / "narrow_rows.xlsx"
    wb = Workbook(); ws = wb.active
    for i in range(20):
        ws.append([i])  # single short column — negligible character cost
    wb.save(str(p))

    md, _ = doc_office.convert(p, tmp_path)
    assert "truncated after 10 of 20 rows" in md
    assert "10-row-per-sheet limit" in md


def test_xlsx_col_cap_bounds_a_single_stray_wide_row(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_office, "XLSX_MAX_COLS", 5)
    from openpyxl import Workbook
    p = tmp_path / "narrow_cols.xlsx"
    wb = Workbook(); ws = wb.active
    ws.append(list(range(20)))  # one row, 20 short columns
    wb.save(str(p))

    md, _ = doc_office.convert(p, tmp_path)
    header_line = next(line for line in md.splitlines() if line.startswith("|"))
    # capped to the first 5 of 20 columns => values 0..4 only, 6 pipes
    # ("| 0 | 1 | 2 | 3 | 4 |")
    assert header_line.count("|") == 6
    assert "4 |" in header_line
    assert "5 |" not in header_line


# --------------------------------------------------------------------------
# Item 2: .docx conversion must be bounded in time and memory — walking
# `document.element.body` fully and formatting every block before
# truncating was a verified unbounded-cost bug (measured ~0.4ms/paragraph,
# 111.65s at 300,000 paragraphs in a plain 0.84MB file). See the module
# docstring for the full writeup.
# --------------------------------------------------------------------------

def _build_docx_with_paragraphs(path, count):
    """Build `count` trivial paragraphs directly via lxml rather than
    `Document.add_paragraph` in a loop — the latter is itself slow enough at
    this scale (multiple minutes for 300,000 calls) to make a perf test
    built that way unusable; this stays under ~2.5s so the test itself
    measures doc_office, not python-docx's own paragraph-insertion cost."""
    from docx import Document
    from docx.oxml.ns import qn
    from lxml import etree

    d = Document()
    body = d.element.body
    sect_pr = body.find(qn("w:sectPr"))
    for i in range(count):
        p = etree.SubElement(body, qn("w:p"))
        r = etree.SubElement(p, qn("w:r"))
        t = etree.SubElement(r, qn("w:t"))
        t.text = f"paragraph number {i} with a bit of body text in it"
    if sect_pr is not None:
        body.remove(sect_pr)
        body.append(sect_pr)
    d.save(str(path))


def test_docx_huge_paragraph_count_converts_quickly_and_bounded(tmp_path):
    p = tmp_path / "huge.docx"
    _build_docx_with_paragraphs(p, 300_000)

    t0 = time.time()
    md, images = doc_office.convert(p, tmp_path)
    elapsed = time.time() - t0

    assert images == []
    assert "paragraph number 0 " in md
    assert "truncated" in md.lower()
    assert len(md) < 35_000  # bounded to roughly MAX_CHARS, not 300,000 paragraphs' worth
    assert elapsed < 5.0, f"conversion took {elapsed:.2f}s — should be well under a second"


def test_docx_single_enormous_table_is_capped(tmp_path):
    """A single pathological table must not be rendered in full before the
    character budget (or DOCX_MAX_TABLE_ROWS) ever gets a chance to reject
    it — previously a 20,000x6 table cost ~4.4s / +52MB RSS regardless of
    MAX_CHARS."""
    from docx import Document
    p = tmp_path / "big_table.docx"
    d = Document()
    t = d.add_table(rows=3000, cols=4)
    for r in t.rows:
        for c in r.cells:
            c.text = "x"
    d.save(str(p))

    t0 = time.time()
    md, _ = doc_office.convert(p, tmp_path)
    elapsed = time.time() - t0

    assert "truncated after" in md.lower()
    assert len(md) < 35_000
    assert elapsed < 5.0, f"conversion took {elapsed:.2f}s — should be a small fraction of a second"
