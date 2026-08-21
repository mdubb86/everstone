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
