import pytest

from es.capabilities import doc_pdf


def test_extracts_text_with_page_headings(text_pdf, tmp_path):
    md, images = doc_pdf.convert(text_pdf, tmp_path)
    assert "## Page 1" in md
    assert "## Page 2" in md
    assert "Fall Season Schedule" in md
    assert "Game 1" in md
    assert images == []


def test_scanned_page_is_rendered_not_transcribed(scanned_pdf, tmp_path):
    md, images = doc_pdf.convert(scanned_pdf, tmp_path)
    assert len(images) == 1
    assert images[0].name == "p001.png"
    assert images[0].is_file()
    assert f"]({images[0]})" in md, "page image must be linked inline"


def test_page_selection_limits_output(text_pdf, tmp_path):
    md, _ = doc_pdf.convert(text_pdf, tmp_path, pages=[2])
    assert "## Page 2" in md
    assert "## Page 1" not in md


def test_page_count_is_reported(text_pdf, tmp_path):
    assert doc_pdf.page_count(text_pdf) == 2


def test_render_produces_one_png_per_requested_page(text_pdf, tmp_path):
    images = doc_pdf.render(text_pdf, tmp_path, pages=[1, 2])
    assert [i.name for i in images] == ["p001.png", "p002.png"]
    assert all(i.is_file() for i in images)


def test_table_renders_once_as_pipe_table_not_duplicated_as_prose(table_pdf, tmp_path):
    md, images = doc_pdf.convert(table_pdf, tmp_path)
    assert images == []
    assert "Roster" in md
    # The table must appear as a pipe table...
    assert "| Name | Position" in md
    # ...and its row content must not ALSO appear as duplicated prose text
    # (regression for issue: extract_text() already includes cell text, so
    # naively appending both the prose and the table doubles every table).
    assert md.count("Alice") == 1
    assert md.count("Bob") == 1


def test_table_cell_pipe_is_escaped(table_pdf, tmp_path):
    md, _ = doc_pdf.convert(table_pdf, tmp_path)
    assert r"Forward\|Winger" in md
    # unescaped, this would misalign the pipe-table column count
    assert "Forward|Winger" not in md


def test_page_of_only_a_table_is_not_misclassified_as_image(table_only_pdf, tmp_path):
    """A page whose only content is a table has near-empty non-table prose
    text; the blank-page decision must be based on the FULL page text (which
    includes the table's own cell content), not the table-excluded text —
    otherwise this page would be wrongly rasterized to a PNG instead of
    rendered as a Markdown table."""
    md, images = doc_pdf.convert(table_only_pdf, tmp_path)
    assert images == [], "an all-table page must not be rendered as an image"
    assert "| Name | Position" in md
    assert "Alice" in md


def test_meaningful_image_on_text_page_gets_render_note(mixed_content_pdf, tmp_path):
    md, images = doc_pdf.convert(mixed_content_pdf, tmp_path, pages=[1])
    assert images == [], "a page with enough text must not be auto-rendered"
    assert "es_doc_render" in md
    assert 'pages="1"' in md


def test_small_decorative_image_gets_no_render_note(mixed_content_pdf, tmp_path):
    md, images = doc_pdf.convert(mixed_content_pdf, tmp_path, pages=[2])
    assert images == []
    assert "es_doc_render" not in md


def test_auto_render_cap_is_enforced(tmp_path, monkeypatch):
    """More image-only pages than the cap: rendering stops at the cap, and
    every page beyond it gets a note pointing at es_doc_render instead of a
    silently-dropped image."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    png = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), (200, 200, 200)).save(png)
    src = tmp_path / "scans.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    for _ in range(4):
        c.drawImage(str(png), 72, 400, width=400, height=300)
        c.showPage()
    c.save()

    monkeypatch.setattr(doc_pdf, "MAX_AUTO_RENDER_PAGES", 2)
    md, images = doc_pdf.convert(src, tmp_path)

    assert len(images) == 2
    for idx in (3, 4):
        assert f'es_doc_render with pages="{idx}"' in md


def test_render_rejects_out_of_range_page(text_pdf, tmp_path):
    with pytest.raises(ValueError, match="1-2"):
        doc_pdf.render(text_pdf, tmp_path, pages=[99])
