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
    """The headline case: a page with NO text layer and one embedded image.
    No blank-page detection is involved any more — the page's section must
    contain the image link and NOTHING ELSE (no heading text, no note)."""
    md, images = doc_pdf.convert(scanned_pdf, tmp_path)
    assert len(images) == 1
    assert images[0].name == "p001-i01.png"
    assert images[0].is_file()
    assert f"]({images[0]})" in md, "page image must be linked inline"
    section = md.split("## Page 1", 1)[1].strip()
    assert section == f"![page 1 image 1]({images[0]})"


def test_one_embedded_photo_produces_one_png_and_one_link(photo_page_pdf, tmp_path):
    md, images = doc_pdf.convert(photo_page_pdf, tmp_path)
    assert len(images) == 1
    assert images[0].is_file()
    assert f"]({images[0]})" in md


def test_image_position_is_interleaved_with_surrounding_text(text_photo_text_pdf, tmp_path):
    """The image link must sit at the image's position in the reading
    order — interleaved with the text — not appended after everything."""
    md, images = doc_pdf.convert(text_photo_text_pdf, tmp_path)
    assert len(images) == 1
    link = f"]({images[0]})"
    intro_pos = md.index("INTRO TEXT ABOVE THE PHOTO")
    link_pos = md.index(link)
    outro_pos = md.index("OUTRO TEXT BELOW THE PHOTO")
    assert intro_pos < link_pos < outro_pos


def test_two_images_on_one_page_produce_two_files_and_two_links(two_images_pdf, tmp_path):
    md, images = doc_pdf.convert(two_images_pdf, tmp_path)
    assert len(images) == 2
    assert len({str(p) for p in images}) == 2, "each image must be a distinct file"
    for img in images:
        assert img.is_file()
        assert f"]({img})" in md


def test_page_with_no_image_gets_no_link(text_pdf, tmp_path):
    md, images = doc_pdf.convert(text_pdf, tmp_path)
    assert images == []
    assert "![" not in md


def test_image_only_on_second_page_is_linked_only_in_that_section(
        image_on_second_page_pdf, tmp_path):
    md, images = doc_pdf.convert(image_on_second_page_pdf, tmp_path)
    assert len(images) == 1
    page1_section, page2_section = md.split("## Page 2", 1)
    assert f"]({images[0]})" not in page1_section
    assert f"]({images[0]})" in page2_section


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


def test_image_extraction_ceiling_is_enforced_and_reported_in_band(tmp_path, monkeypatch):
    """More embedded images across the document than MAX_EXTRACTED_IMAGES:
    extraction stops at the ceiling, and every image beyond it is reported
    in-band (a note pointing at es_doc_render) rather than silently dropped.
    This replaces the old per-PAGE auto-render cap (deleted along with the
    blank-page auto-render branch it protected) with a per-IMAGE ceiling —
    every page can now contribute more than one image, so the resource risk
    that needs bounding is the image count, not the page count."""
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

    monkeypatch.setattr(doc_pdf, "MAX_EXTRACTED_IMAGES", 2)
    md, images = doc_pdf.convert(src, tmp_path)

    assert len(images) == 2
    for idx in (3, 4):
        assert f'es_doc_render with pages="{idx}"' in md
    assert "not extracted" in md


def test_render_rejects_out_of_range_page(text_pdf, tmp_path):
    with pytest.raises(ValueError, match="1-2"):
        doc_pdf.render(text_pdf, tmp_path, pages=[99])


def test_extracted_image_preserves_native_resolution_over_small_display(tmp_path):
    """A source image stored at high native resolution but placed small on
    the page must be extracted at (close to) its native resolution, not
    downsampled to the small on-page display size — otherwise a legible
    high-res scan placed as a thumbnail would come out blurry."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    native = tmp_path / "native.png"
    Image.new("RGB", (1200, 800), (100, 150, 200)).save(native)
    src = tmp_path / "small_display.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.drawImage(str(native), 72, 400, width=150, height=100)  # displayed tiny
    c.showPage()
    c.save()

    _, images = doc_pdf.convert(src, tmp_path)
    assert len(images) == 1
    from PIL import Image as PILImage
    out = PILImage.open(images[0])
    # Native is 1200x800; a naive full-page raster at RENDER_DPI (150/72)
    # would only produce roughly 150*(150/72) =~ 312px wide for this
    # placement — assert we're well above that, i.e. close to native.
    assert out.size[0] >= 1000


def test_extracted_image_pixel_color_matches_source(photo_page_pdf, tmp_path):
    """Sanity check on the extraction path itself: the saved PNG must
    actually contain the source image's pixels, not a blank/garbage crop."""
    from PIL import Image as PILImage
    _, images = doc_pdf.convert(photo_page_pdf, tmp_path)
    out = PILImage.open(images[0]).convert("RGB")
    center = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert center == (100, 150, 200)
