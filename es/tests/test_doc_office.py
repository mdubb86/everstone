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
