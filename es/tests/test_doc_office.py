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


# --------------------------------------------------------------------------
# Embedded images — every image comes out as the thing it is (a file, linked
# inline at its position), because it exists, not because it scored above a
# threshold. Mirrors doc_pdf's own image-extraction test shape.
# --------------------------------------------------------------------------

def test_docx_inline_image_extracted_and_linked_between_paragraphs(
        docx_with_inline_image, tmp_path):
    md, images = doc_office.convert(docx_with_inline_image, tmp_path)

    assert len(images) == 1
    assert images[0].exists()
    assert images[0].suffix == ".png"

    first_idx = md.index("FIRST paragraph")
    image_idx = md.index("![embedded image 1]")
    last_idx = md.index("LAST paragraph")
    assert first_idx < image_idx < last_idx


def test_docx_no_images_means_no_files_and_no_links(docx_file, tmp_path):
    """docx_file (the base fixture) has headings/paragraphs/a table but no
    embedded pictures at all — must not fabricate a file or a link."""
    md, images = doc_office.convert(docx_file, tmp_path)
    assert images == []
    assert "![" not in md


def test_docx_two_images_two_files_two_links_in_document_order(
        docx_with_two_images, tmp_path):
    md, images = doc_office.convert(docx_with_two_images, tmp_path)

    assert len(images) == 2
    assert images[0] != images[1]
    for img in images:
        assert img.exists()

    first_link_idx = md.index("![embedded image 1]")
    between_idx = md.index("BETWEEN THE TWO PHOTOS")
    second_link_idx = md.index("![embedded image 2]")
    assert first_link_idx < between_idx < second_link_idx


def test_docx_table_cell_image_emitted_after_table_not_inside_cell(
        docx_with_table_cell_image, tmp_path):
    """A Markdown link inside a pipe-table cell risks corrupting the table's
    own syntax (see the module docstring's "WHERE AN IMAGE LINK LANDS"
    note) — so a cell's image is reported as its own block AFTER the whole
    table, naming which row/column it came from, rather than embedded in
    the cell itself."""
    md, images = doc_office.convert(docx_with_table_cell_image, tmp_path)

    assert len(images) == 1
    assert images[0].exists()

    # The cell itself stays plain text — no link, no broken pipe syntax.
    table_line = next(line for line in md.splitlines()
                       if line.startswith("| Widget"))
    assert "![" not in table_line
    assert table_line.count("|") == 3  # "| Widget |  |" — unbroken

    # The image is reported after the table, naming its cell.
    table_idx = md.index("| Widget")
    image_idx = md.index("![embedded image 1")
    assert table_idx < image_idx
    assert "row 2, column 2" in md


def test_docx_duplicate_image_relationship_writes_one_file_two_links(
        docx_with_duplicated_image_relationship, tmp_path):
    """The SAME `r:embed` relationship id referenced from two paragraphs
    (e.g. a letterhead logo at the top and bottom of a template) is the SAME
    underlying image part by construction — python-docx's own model already
    asserts this, it isn't an ambiguous "same bytes, is that one image or
    two" judgment call. Writing it to disk twice would be pure waste with
    no benefit to the agent (same picture either way); each of the two
    APPEARANCES in the reading order still gets its own link, both pointing
    at the one file that was actually written."""
    md, images = doc_office.convert(
        docx_with_duplicated_image_relationship, tmp_path)

    assert len(images) == 1
    assert images[0].exists()
    assert md.count("![embedded image 1]") == 2

    para_a_idx = md.index("Para A")
    first_link_idx = md.index("![embedded image 1]")
    para_b_idx = md.index("Para B")
    second_link_idx = md.rindex("![embedded image 1]")
    assert para_a_idx < first_link_idx < para_b_idx < second_link_idx


def test_docx_image_extraction_ceiling_is_enforced_and_reported_in_band(
        tmp_path, monkeypatch):
    """A pathological document (far more embedded images than any real one
    would carry) must not write an unbounded number of files — and hitting
    the ceiling must be reported IN-BAND, never a silent drop. Monkeypatches
    the ceiling down (mirroring doc_pdf's own ceiling test) rather than
    building 500+ real images, which would make this test both slow and an
    unfaithful stand-in for the real limit's *shape*, not its exact value."""
    import io
    from docx import Document
    from PIL import Image

    monkeypatch.setattr(doc_office, "MAX_EXTRACTED_IMAGES", 3)

    p = tmp_path / "many_images.docx"
    d = Document()
    for i in range(5):
        photo = io.BytesIO()
        Image.new("RGB", (10, 10), (i, i, i)).save(photo, format="PNG")
        photo.seek(0)
        d.add_paragraph(f"before photo {i}")
        d.add_picture(photo)
    d.save(str(p))

    md, images = doc_office.convert(p, tmp_path)

    assert len(images) == 3  # only the ceiling's worth actually written
    assert "![embedded image 1]" in md
    assert "![embedded image 3]" in md
    assert "![embedded image 4]" not in md
    assert "2 further" in md
    assert "not extracted" in md
    assert "limit of 3 images" in md


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


# --------------------------------------------------------------------------
# Item 4: the row/column structural caps, exercised on their own — narrow
# rows so the character budget never fires first and can't mask a broken
# cap. monkeypatch keeps this independent of the module's real constants.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# .docx conversion must be bounded in TIME and memory even though it is no
# longer bounded in OUTPUT SIZE — walking `document.element.body` fully via
# python-docx's own `Paragraph.style`/`Paragraph.text` properties (each of
# which re-resolves from scratch, via an xpath call, on every single access)
# was a verified O(document size) cost regardless of how much output
# survived any budget: measured ~0.4ms/paragraph, 111.65s at 300,000
# paragraphs in a plain 0.84MB file. The fix resolves both once (a style-id
# -> heading-level map, and a direct-lxml text/style-id reader) instead of
# racing a small budget against a slow per-access cost — so full,
# UNTRUNCATED conversion of the same 300,000-paragraph document is now the
# thing under test, not truncation. See the module docstring for the full
# writeup and the measured before/after at 10k/100k/300k paragraphs.
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
    """Was truncation-at-30,000-characters: MAX_CHARS is now a generous
    resource ceiling (bound resources, not context), so this converts EVERY
    paragraph — the point of this test is that doing so is still fast,
    which is only true because `_build_heading_levels`/`_paragraph_text`/
    `_paragraph_style_id` removed the per-paragraph xpath costs that used
    to make full conversion of a document this size take 111.65s (see the
    module docstring). Measured on this machine after the fix: ~2.0s for
    this exact 300,000-paragraph document — the assertion below leaves
    generous headroom above that so the test itself doesn't flake, while
    still catching a real regression back toward the old per-access cost
    (which would fail this at well over a minute)."""
    p = tmp_path / "huge.docx"
    _build_docx_with_paragraphs(p, 300_000)

    t0 = time.time()
    md, images = doc_office.convert(p, tmp_path)
    elapsed = time.time() - t0

    assert images == []
    assert "paragraph number 0 " in md
    assert "paragraph number 299999 " in md  # every paragraph survives — full conversion
    assert "truncated" not in md.lower()
    assert elapsed < 15.0, f"conversion took {elapsed:.2f}s — should be a few seconds at most"


def test_docx_single_enormous_table_is_capped(tmp_path):
    """A single pathological table must not be rendered in full before
    DOCX_MAX_TABLE_ROWS (independent of MAX_CHARS — see the module
    docstring) ever gets a chance to reject it — previously a 20,000x6
    table cost ~4.4s / +52MB RSS regardless of any character budget.
    DOCX_MAX_TABLE_ROWS (2000) is unchanged by today's "convert fully"
    redesign — unlike MAX_CHARS, it bounds a genuinely separate,
    per-block cost (constructing real python-docx cell/paragraph/run
    objects to read `.text`, proportional to table size), not a
    context-window number — so a 3000-row table still truncates at 2000
    rows. The expected output size below is therefore sized to
    DOCX_MAX_TABLE_ROWS (~18 bytes/row for this fixture's narrow "| x | x |
    x | x |" rows), not to the old MAX_CHARS."""
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

    assert "truncated after 2000 rows" in md.lower()
    assert len(md) < 45_000  # ~2000 rows of "| x | x | x | x |", not 3000
    assert elapsed < 5.0, f"conversion took {elapsed:.2f}s — should be a small fraction of a second"


# --------------------------------------------------------------------------
# "Convert fully, bound resources not context" — real-shaped documents that
# would previously have lost most of their content to MAX_CHARS now convert
# in full against the DEFAULT (generous) ceiling, no monkeypatching needed.
# --------------------------------------------------------------------------

