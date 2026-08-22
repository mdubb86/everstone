import asyncio
import os
import re
import time

import pytest

from es.capabilities import docs


# --- fixtures for malformed/unusual PDFs -----------------------------------
# Kept local to this module (not conftest.py) since these exercise docs.py's
# own error-mapping, not doc_pdf's conversion logic.

@pytest.fixture
def encrypted_pdf(tmp_path):
    """A real password-protected PDF (reportlab supports this natively —
    no extra dependency needed)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.pdfgen import canvas

    p = tmp_path / "encrypted.pdf"
    enc = StandardEncryption("userpw", ownerPassword="ownerpw", canPrint=1)
    c = canvas.Canvas(str(p), pagesize=letter, encrypt=enc)
    c.drawString(72, 720, "secret")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def corrupt_pdf(tmp_path):
    """Has the %PDF magic bytes but no usable structure behind them."""
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"%PDF-1.4\n%truncated, no xref table, no /Root")
    return p


@pytest.fixture
def empty_pdf(tmp_path):
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    return p


@pytest.fixture
def fake_pdf(tmp_path):
    """A plain text file wearing a .pdf extension."""
    p = tmp_path / "fake.pdf"
    p.write_text("this is just a text file renamed to .pdf\n" * 5)
    return p


@pytest.fixture
def one_scanned_one_text_pdf(tmp_path):
    """Page 1 is image-only (auto-rendered by extract); page 2 has a real
    text layer. Gives extract() a non-empty images.json (from page 1) that
    is distinguishable from a PNG a LATER es_doc_render call would drop into
    the same artifact dir for page 2."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    png = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), (200, 200, 200)).save(png)
    p = tmp_path / "one_scanned_one_text.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawImage(str(png), 72, 400, width=400, height=300)
    c.showPage()
    c.drawString(72, 720, "Page two has a real text layer.")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def zero_page_pdf(tmp_path):
    """Opens fine as a PDF but legitimately has zero pages."""
    from reportlab.pdfgen import canvas

    p = tmp_path / "zero.pdf"
    canvas.Canvas(str(p)).save()
    return p


# --- extract() ---------------------------------------------------------

def test_extract_returns_markdown_and_doc_id(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert len(out["doc_id"]) == 12
    assert out["kind"] == "pdf"
    assert out["page_count"] == 2
    assert "Fall Season Schedule" in out["markdown"]
    assert out["truncated"] is False


def test_extract_rejects_path_outside_roots(text_pdf, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(docs.paths.SourceForbidden):
        docs.extract(str(text_pdf), roots=[other], cache_root=tmp_path)


def test_extract_rejects_unsupported_extension(tmp_path):
    f = tmp_path / "notes.rtf"
    f.write_text("x")
    with pytest.raises(docs.UnsupportedDocument) as e:
        docs.extract(str(f), roots=[tmp_path], cache_root=tmp_path)
    assert ".rtf" in str(e.value)


def test_extract_writes_markdown_into_the_cache(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    cached = tmp_path / ".es" / out["doc_id"] / "doc.md"
    assert cached.is_file()
    assert "Fall Season Schedule" in cached.read_text(encoding="utf-8")


def test_extract_truncates_oversized_output_but_caches_the_full_markdown(
        text_pdf, tmp_path, monkeypatch):
    """Regression guard for writing the TRUNCATED copy to disk: this uses the
    SAME call/fixture for both assertions, so a future change that truncates
    before writing doc.md would fail here (a version that truncated first and
    used a different fixture for the doc.md check could pass both tests
    without ever proving the two stay in sync)."""
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", 40)
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert out["truncated"] is True
    # The content actually kept (everything before the appended resume
    # marker) stays bounded by the limit; the marker itself is allowed to
    # push the total past it (it's operator text, not document content).
    assert len(out["markdown"].rsplit("\n\n*(truncated", 1)[0]) <= 40

    cached = (tmp_path / ".es" / out["doc_id"] / "doc.md").read_text(encoding="utf-8")
    assert len(cached) > 40
    assert "Fall Season Schedule" in cached


def test_truncation_cuts_at_a_page_boundary_with_a_correct_usable_resume_range(
        text_pdf, tmp_path, monkeypatch):
    """When truncation lands past page 1, the cut must land exactly at the
    "## Page N" heading boundary (not mid-page), and the resume marker must
    name a page range that (a) picks up exactly where output stopped and
    (b) actually works if the agent uses it."""
    baseline = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert baseline["truncated"] is False
    boundary = baseline["markdown"].index("\n\n## Page 2")
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", boundary + 2)

    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert out["truncated"] is True
    assert out["markdown"].endswith(
        '*(truncated after page 1 of 2 — call es_doc_extract again with '
        'pages="2-2" to continue)*')
    # The cut landed cleanly at the page boundary: page 1's content survives
    # whole, page 2's content is entirely gone (not a partial fragment of it).
    assert "Fall Season Schedule" in out["markdown"]
    assert "Game 1" not in out["markdown"]

    # The marker's suggested range is actually usable.
    resumed = docs.extract(str(text_pdf), roots=[text_pdf.parent],
                           cache_root=tmp_path, pages="2-2")
    assert "Game 1" in resumed["markdown"]


def test_truncation_never_splits_a_markdown_image_link(tmp_path, monkeypatch):
    """Build a multi-page all-image PDF long enough that a naive
    markdown[:MAX_MARKDOWN_CHARS] slice would land inside one of the
    "![page N](/long/tmp/path/pNNN.png)" links; the fix must cut at a page
    boundary instead, so no link is ever left half-written."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    png = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), (200, 200, 200)).save(png)
    pdf = tmp_path / "all_scanned.pdf"
    c = canvas.Canvas(str(pdf), pagesize=letter)
    for _ in range(5):
        c.drawImage(str(png), 72, 400, width=400, height=300)
        c.showPage()
    c.save()

    baseline = docs.extract(str(pdf), roots=[tmp_path], cache_root=tmp_path)
    assert baseline["truncated"] is False
    link_start = baseline["markdown"].index("![page 3]")
    # Land the limit squarely inside page 3's image link.
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", link_start + 5)

    out = docs.extract(str(pdf), roots=[tmp_path], cache_root=tmp_path)
    assert out["truncated"] is True
    for m in re.finditer(r"!\[", out["markdown"]):
        tail = out["markdown"][m.start():]
        assert re.match(r"!\[[^\]]*\]\([^)]*\)", tail), \
            "an image link was cut in half by truncation"


def test_truncation_when_even_page_one_alone_exceeds_the_limit(text_pdf, tmp_path, monkeypatch):
    """No earlier page boundary exists to cut at, so this falls back to a hard
    cut. It must still say why (rather than silently truncating) and must not
    offer a page-range resume marker, since re-requesting page 1 alone would
    reproduce the identical oversized page."""
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", 10)
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert out["truncated"] is True
    assert "page 1" in out["markdown"].lower()
    assert 'pages="' not in out["markdown"]


# --- outer 40k marker must not name an impossible remedy on flat formats ---
# (item 4: the marker must not mention "page"/es_doc_render for a format
# that has no pages and for which es_doc_render always raises
# UnsupportedDocument by design.) Each fixture below builds a SINGLE
# indivisible block (one CSV header row / one calendar event / one paragraph
# / one spreadsheet row) that alone exceeds MAX_MARKDOWN_CHARS — every one of
# these converters unconditionally keeps its first block regardless of size
# (mirroring doc_pdf's own "page 1 alone" case), so none of them self-
# truncates first; docs.py's own outer cap is what fires here.

def _assert_flat_format_overflow_marker(markdown: str) -> None:
    assert "es_doc_render" not in markdown
    assert 'pages="' not in markdown
    assert "page " not in markdown.lower()
    assert "no narrower view to fall back to" in markdown


def test_outer_truncation_marker_is_format_aware_for_csv(tmp_path):
    """8000-column CSV: the header row alone is a single indivisible block
    far over the 40k limit, with no earlier row boundary to cut at."""
    header = ",".join(f"col_{i:05d}" for i in range(8000))
    p = tmp_path / "wide.csv"
    p.write_text(header + "\n", encoding="utf-8")
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert out["truncated"] is True
    assert out["page_count"] is None
    _assert_flat_format_overflow_marker(out["markdown"])


def test_outer_truncation_marker_is_format_aware_for_ics(tmp_path):
    """One VEVENT with a giant DESCRIPTION: a single event is doc_ics's
    indivisible block, and it alone exceeds the limit."""
    huge = "x" * 45_000
    p = tmp_path / "huge_event.ics"
    p.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:Huge event\r\n"
        "DTSTART:20260905T140000Z\r\n"
        f"DESCRIPTION:{huge}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n", encoding="utf-8")
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert out["truncated"] is True
    assert out["page_count"] is None
    _assert_flat_format_overflow_marker(out["markdown"])


def test_outer_truncation_marker_is_format_aware_for_docx(tmp_path):
    """One giant paragraph: a single paragraph is doc_office's indivisible
    block for .docx, and it alone exceeds the limit."""
    from docx import Document

    p = tmp_path / "huge_paragraph.docx"
    d = Document()
    d.add_paragraph("word " * 10_000)
    d.save(str(p))
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert out["truncated"] is True
    assert out["page_count"] is None
    _assert_flat_format_overflow_marker(out["markdown"])


def test_outer_truncation_marker_is_format_aware_for_xlsx(tmp_path):
    """One giant row: a single row is doc_office's indivisible block for
    .xlsx, and it alone exceeds the limit. A wide row of many COLUMNS would
    be capped at XLSX_MAX_COLS (256) before ever reaching this size, and a
    single CELL is capped at Excel's own real 32,767-character limit
    (enforced by openpyxl itself on save) — so this uses two near-max cells
    in one row instead — still one row, one indivisible block, well under
    the 256-column cap, comfortably over the 40k character limit combined."""
    from openpyxl import Workbook

    p = tmp_path / "huge_row.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["x" * 32_000, "y" * 32_000])
    wb.save(str(p))
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert out["truncated"] is True
    assert out["page_count"] is None
    _assert_flat_format_overflow_marker(out["markdown"])


# --- truncated must be True whenever a converter self-truncated, even when ---
# --- the RESULT stays under the outer 40k cap (item 2) ----------------------
# Every converter below truncates ITSELF at its own smaller budget (~30k) and
# says so in-band; before this fix, docs.py's `truncated` flag only ever
# reflected its OWN 40k outer cap, so it stayed False even though the agent
# was handed less than the full document.

def test_self_truncation_is_reported_for_csv(tmp_path):
    rows = "\n".join(f"{i},value-{i}" for i in range(6000))
    p = tmp_path / "many_rows.csv"
    p.write_text("id,value\n" + rows + "\n", encoding="utf-8")
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS  # outer cap never fired
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]


def test_self_truncation_is_reported_for_json(tmp_path):
    import json as jsonlib
    data = [{"i": i, "note": "padding text to grow each entry a bit"} for i in range(1200)]
    p = tmp_path / "big.json"
    p.write_text(jsonlib.dumps(data), encoding="utf-8")
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]


def test_self_truncation_is_reported_for_txt(tmp_path):
    line = "x" * 39 + "\n"
    p = tmp_path / "big.txt"
    p.write_text(line * 1000, encoding="utf-8")
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]


def test_self_truncation_is_reported_for_md(tmp_path):
    line = "x" * 39 + "\n"
    p = tmp_path / "big.md"
    p.write_text(line * 1000, encoding="utf-8")
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]


def test_self_truncation_is_reported_for_ics(tmp_path):
    events = []
    for i in range(500):
        events.append(
            "BEGIN:VEVENT\r\nUID:{n}\r\nSUMMARY:Game {n} vs Team {n}\r\n"
            "DTSTART:202609{d:02d}T140000Z\r\nLOCATION:Field {n}\r\n"
            "END:VEVENT\r\n".format(n=i, d=(i % 28) + 1))
    p = tmp_path / "many_events.ics"
    p.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        + "".join(events) + "END:VCALENDAR\r\n", encoding="utf-8")
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]


def test_self_truncation_is_reported_for_docx(tmp_path):
    from docx import Document

    p = tmp_path / "many_paragraphs.docx"
    d = Document()
    for i in range(1200):
        d.add_paragraph(f"Paragraph number {i} with some filler text to pad it out.")
    d.save(str(p))
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]


def test_self_truncation_is_reported_for_xlsx(tmp_path):
    from openpyxl import Workbook

    p = tmp_path / "many_rows.xlsx"
    wb = Workbook()
    ws = wb.active
    for i in range(3000):
        ws.append([f"row{i:05d}", f"val-{i:05d}", f"val-{i:05d}"])
    wb.save(str(p))
    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]


def test_self_truncation_is_reported_for_write_only_xlsx_with_no_dimension(tmp_path):
    """Regression guard for a LIVE bug: `openpyxl.Workbook(write_only=True)`
    never writes a `<dimension>` element to a sheet's XML (see doc_office's
    module docstring), so this sheet's true row count can never be
    determined without a full scan — the exact case
    doc_office._sheet_truncation_note's "total_rows is None" branch exists
    for. That branch's marker embeds a NESTED parenthetical aside ("...could
    not be determined (its XML has no declared dimension)"), and before this
    fix docs.py detected self-truncation with a regex that required NO
    parentheses at all between "*(" and the closing ")*" — so this exact
    marker was never detected, and `truncated` came back False.

    Verified live in the running container before this fix: a 5,000-row
    write_only workbook rendered only 856 rows (the rest silently dropped)
    and the es_doc_extract envelope reported `truncated: false` — the worst
    failure mode for this tool, a successful-looking result with wrong
    content. This is the test that would have caught it.
    """
    from openpyxl import Workbook

    p = tmp_path / "write_only_many_rows.xlsx"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Sheet")
    for i in range(5000):
        ws.append([f"row{i:05d}", f"val-{i:05d}", f"val-{i:05d}"])
    wb.save(str(p))

    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert len(out["markdown"]) < docs.MAX_MARKDOWN_CHARS  # outer cap never fired
    assert out["truncated"] is True
    assert "truncated after" in out["markdown"]
    assert "no declared dimension" in out["markdown"]


def test_small_write_only_xlsx_with_no_dimension_is_not_falsely_truncated(tmp_path):
    """The other half of the write_only regression above: a small
    dimension-less sheet must report `truncated: False`. Found while
    manually verifying this fix — `doc_office._sheet_truncation_note` used
    to infer "was this cut short?" from `kept < capped_rows`, where
    `capped_rows` was silently the bare XLSX_MAX_ROWS fallback ceiling (not
    this sheet's real size) whenever the dimension was unknown, so even a
    2-row write_only sheet satisfied that comparison and was falsely
    reported as truncated. Fixed alongside the detection bug since both
    live in the same code path this task touches."""
    from openpyxl import Workbook

    p = tmp_path / "tiny_write_only.xlsx"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Sheet")
    ws.append(["a", "b"])
    ws.append(["c", "d"])
    wb.save(str(p))

    out = docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert out["truncated"] is False
    assert "truncated" not in out["markdown"].lower()


def test_self_truncation_detection_is_a_sentinel_check_not_a_regex(tmp_path):
    """The structural fix for the write_only-xlsx bug above: detection must
    not depend on parsing a converter's own prose at all. Build a marker
    (via the same doc_support.truncation_marker every converter now uses)
    whose detail text contains deeply nested parentheses far beyond what any
    real converter emits today — proving detection is a plain check for
    doc_support.TRUNCATION_SENTINEL, not a regex reconstructing balanced
    parens around free-form text that could break again the next time a
    converter's message is reworded."""
    from es.capabilities import doc_support

    markdown = "some real document content\n\n" + doc_support.truncation_marker(
        "after 1 (of an unknown total (nested (again) for good measure)) rows")
    assert docs._converter_self_truncated(markdown) is True

    # And the inverse: ordinary content that merely contains the word
    # "truncated" (no marker, no sentinel) must NOT be mistaken for one —
    # the fix must not trade a false negative for a false positive.
    assert docs._converter_self_truncated(
        "the report was truncated by the printer, not by us") is False


def test_extract_purges_stale_artifacts(text_pdf, tmp_path):
    from es import doc_cache
    stale = doc_cache.artifact_dir(tmp_path, "stale0000000")
    old = time.time() - (25 * 3600)
    os.utime(stale, (old, old))
    docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert not stale.exists()


def test_extract_narrows_to_requested_pages(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent],
                        cache_root=tmp_path, pages="1")
    assert out["page_count"] == 2  # total pages in the document
    assert "Fall Season Schedule" in out["markdown"]
    assert "Game 1" not in out["markdown"]  # page 2's content, excluded


def test_extract_second_call_is_a_cache_hit_not_a_reconvert(text_pdf, tmp_path, monkeypatch):
    calls = []
    original = docs.doc_pdf.convert

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(docs.doc_pdf, "convert", spy)

    first = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    second = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)

    assert len(calls) == 1  # convert() ran once; the second call was a cache hit
    assert second["markdown"] == first["markdown"]
    assert second["doc_id"] == first["doc_id"]


def test_extract_recovers_from_a_corrupted_cached_doc_md(text_pdf, tmp_path):
    """doc_id is a CONTENT hash: if doc.md was left truncated by a crash or
    ENOSPC, re-sending the identical PDF lands in the same artifact dir and
    must NOT raise (and must not stay broken for the 24h TTL) — an
    undecodable/unreadable doc.md must be treated as a cache miss, the same
    way a broken images.json already is, and the entry overwritten with a
    good reconversion."""
    first = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    md_path = tmp_path / ".es" / first["doc_id"] / "doc.md"
    md_path.write_bytes(b"\xff\xfe\x00 not valid utf-8 \x80\x81")

    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert "Fall Season Schedule" in out["markdown"]

    # The bad cache entry was overwritten with the good reconversion.
    assert "Fall Season Schedule" in md_path.read_text(encoding="utf-8")


def test_cache_hit_images_come_from_manifest_not_a_directory_scan(
        one_scanned_one_text_pdf, tmp_path):
    """A cache-hit extract must report only the images the ORIGINAL extract
    produced (from images.json) — not every PNG that happens to sit in the
    artifact dir, including ones a later es_doc_render call drops there for
    pages the extract itself never rendered."""
    first = docs.extract(str(one_scanned_one_text_pdf),
                         roots=[one_scanned_one_text_pdf.parent], cache_root=tmp_path)
    assert len(first["images"]) == 1  # only page 1, the image-only page

    # es_doc_render page 2 into the SAME artifact dir — page 2 has real text
    # and was never rendered by extract(), so its PNG is new to the dir.
    rendered = docs.render(str(one_scanned_one_text_pdf),
                           roots=[one_scanned_one_text_pdf.parent],
                           cache_root=tmp_path, pages="2")
    assert rendered["images"]
    adir = tmp_path / ".es" / first["doc_id"]
    assert len(list(adir.glob("*.png"))) == 2  # both PNGs now physically present

    second = docs.extract(str(one_scanned_one_text_pdf),
                          roots=[one_scanned_one_text_pdf.parent], cache_root=tmp_path)
    assert second["images"] == first["images"]  # unchanged by the render() call


def test_extract_page_subset_does_not_clobber_full_extract_cache(text_pdf, tmp_path):
    full = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    md_path = tmp_path / ".es" / full["doc_id"] / "doc.md"
    before = md_path.read_text(encoding="utf-8")

    subset = docs.extract(str(text_pdf), roots=[text_pdf.parent],
                           cache_root=tmp_path, pages="2")
    assert "Fall Season Schedule" not in subset["markdown"]  # page 1's content

    after = md_path.read_text(encoding="utf-8")
    assert after == before
    assert "Fall Season Schedule" in after  # still the whole document on disk


def test_extract_cache_hit_still_touches_artifact_dir(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    adir = tmp_path / ".es" / out["doc_id"]
    old = time.time() - 1000
    os.utime(adir, (old, old))

    docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)

    assert os.stat(adir).st_mtime > old


def test_extract_rejects_oversized_document(text_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(docs.DocumentTooLarge) as e:
        docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_too_large"


def test_extract_missing_file_mentions_resend(text_pdf, tmp_path):
    missing = text_pdf.parent / "gone.pdf"
    with pytest.raises(docs.paths.SourceNotFound) as e:
        docs.extract(str(missing), roots=[text_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_not_found"
    assert "resend" in str(e.value).lower() or "re-send" in str(e.value).lower()


def test_extract_encrypted_pdf_names_the_remedy(encrypted_pdf, tmp_path):
    with pytest.raises(docs.EncryptedDocument) as e:
        docs.extract(str(encrypted_pdf), roots=[encrypted_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_encrypted"
    assert "password" in str(e.value).lower()


def test_extract_corrupt_pdf_is_unreadable(corrupt_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(corrupt_pdf), roots=[corrupt_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_zero_byte_pdf_is_unreadable(empty_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(empty_pdf), roots=[empty_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_mislabeled_text_file_is_unreadable(fake_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(fake_pdf), roots=[fake_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_zero_page_pdf_is_unreadable(zero_page_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(zero_page_pdf), roots=[zero_page_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_rejects_over_long_path_without_leaking_oserror(tmp_path):
    """A path that resolves inside an allowed root but is too long for the
    filesystem must not surface as a raw OSError (which would leak the full
    path and an opaque 'OSError' code to the agent)."""
    root = tmp_path / "root"
    root.mkdir()
    source = str(root / ("a" * 5000 + ".pdf"))
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(source, roots=[root], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_and_render_agree_on_empty_pages_string(text_pdf, tmp_path):
    """pages="" is a malformed selector in both tools, not a synonym for
    'whole document' — only omitting the argument (None) means that."""
    with pytest.raises(docs.InvalidPageRange):
        docs.extract(str(text_pdf), roots=[text_pdf.parent],
                     cache_root=tmp_path, pages="")
    with pytest.raises(docs.InvalidPageRange):
        docs.render(str(text_pdf), roots=[text_pdf.parent],
                    cache_root=tmp_path, pages="")


# --- render() ------------------------------------------------------------

def test_render_returns_page_images(text_pdf, tmp_path):
    out = docs.render(str(text_pdf), roots=[text_pdf.parent],
                      cache_root=tmp_path, pages="1-2")
    assert len(out["images"]) == 2
    assert out["page_count"] == 2


def _make_pdf(path, n_pages):
    """An n-page PDF with real text on every page (used to check the
    render() default range against documents of varying length)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(n_pages):
        c.drawString(72, 720, f"Page {i + 1} content")
        c.showPage()
    c.save()
    return path


@pytest.mark.parametrize("n_pages", [1, 2, 15])
def test_render_default_pages_clamps_to_the_document_length(tmp_path, n_pages):
    """The default (pages omitted) must never error just because the document
    is shorter than the default 1-10 window — the motivating case is a 1-3
    page schedule. A document at/over the window renders exactly the window."""
    pdf = _make_pdf(tmp_path / f"doc_{n_pages}.pdf", n_pages)
    out = docs.render(str(pdf), roots=[tmp_path], cache_root=tmp_path)
    assert out["page_count"] == n_pages
    assert len(out["images"]) == min(n_pages, 10)


def test_render_explicit_out_of_range_still_errors_even_when_default_would_clamp(tmp_path):
    """Clamping is only for the implicit default. An agent-supplied EXPLICIT
    range past the document's end stays a loud error (matching extract()'s
    explicit-range behavior) rather than silently returning a partial result —
    it more likely names a wrong page than an intentional partial ask."""
    pdf = _make_pdf(tmp_path / "doc_3.pdf", 3)
    with pytest.raises(docs.InvalidPageRange):
        docs.render(str(pdf), roots=[tmp_path], cache_root=tmp_path, pages="1-10")


def test_render_rejects_page_out_of_range(text_pdf, tmp_path):
    with pytest.raises(docs.InvalidPageRange):
        docs.render(str(text_pdf), roots=[text_pdf.parent],
                    cache_root=tmp_path, pages="9")


def test_render_rejects_more_pages_than_the_cap(text_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_RENDER_PAGES", 1)
    with pytest.raises(docs.InvalidPageRange) as e:
        docs.render(str(text_pdf), roots=[text_pdf.parent],
                    cache_root=tmp_path, pages="1-2")
    msg = str(e.value)
    # Specific enough to fail if the cap or the requested-page count regress
    # (the old "1" in str(e.value) assertion would pass on almost any message).
    assert "cannot render 2 pages" in msg
    assert "limit is 1" in msg


# --- parse_pages() ---------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("1", [1]), ("1-3", [1, 2, 3]), ("1-2,5", [1, 2, 5]), ("3,1", [1, 3]),
])
def test_parse_pages(spec, expected):
    assert docs.parse_pages(spec, page_count=10) == expected


def test_parse_pages_rejects_garbage():
    with pytest.raises(docs.InvalidPageRange):
        docs.parse_pages("one", page_count=10)


def test_parse_pages_rejects_reversed_range():
    with pytest.raises(docs.InvalidPageRange, match="reversed"):
        docs.parse_pages("3-1", page_count=3)


def test_parse_pages_rejects_negative_component():
    with pytest.raises(docs.InvalidPageRange, match="starting at 1"):
        docs.parse_pages("1--3", page_count=10)


def test_parse_pages_on_zero_page_document_names_the_document_not_a_range():
    with pytest.raises(docs.InvalidPageRange, match="no pages"):
        docs.parse_pages("1", page_count=0)


# --- MCP layer --------------------------------------------------------

def test_mcp_tools_are_registered():
    """hasattr() would still pass if @mcp.tool() were removed (the plain
    function is still an attribute of the module) — assert real registration
    with the server instead."""
    from es import mcp_server
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"es_doc_extract", "es_doc_render"} <= names


def test_mcp_extract_returns_envelope_on_bad_path(monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [tmp_path])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_extract("/etc/passwd")
    assert out["ok"] is False
    assert out["error"]["code"] in ("doc_not_found", "doc_forbidden")


def test_mcp_extract_returns_envelope_on_success(text_pdf, monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [text_pdf.parent])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_extract(str(text_pdf))
    assert out["ok"] is True
    assert "Fall Season Schedule" in out["data"]["markdown"]


def test_mcp_render_returns_envelope_on_success(text_pdf, monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [text_pdf.parent])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_render(str(text_pdf), pages="1")
    assert out["ok"] is True
    assert len(out["data"]["images"]) == 1


def test_mcp_render_returns_envelope_on_bad_path(monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [tmp_path])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_render("/etc/passwd")
    assert out["ok"] is False
    assert out["error"]["code"] in ("doc_not_found", "doc_forbidden")


def test_doc_cache_root_follows_hermes_home(monkeypatch):
    from es import mcp_server
    monkeypatch.setenv("HERMES_HOME", "/somewhere/else")
    assert str(mcp_server._doc_cache_root()) == \
        "/somewhere/else/profiles/everstone/cache/documents"


def test_doc_roots_returns_media_cache_and_vault_only(tmp_path, monkeypatch):
    """_doc_roots() is the security-relevant seam deciding which directories
    are readable at all; exercise it against real config instead of
    monkeypatching it away, so a regression here (e.g. returning ["/"])
    actually fails a test."""
    from es import mcp_server

    cache_dir = tmp_path / "media-cache"
    vault_dir = tmp_path / "vault"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "obsidian:\n"
        "  vault_name: Vault\n"
        "  attachments:\n"
        f"    sources: [{cache_dir}]\n"
    )
    monkeypatch.setenv("ES_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("ES_VAULT_PATH", str(vault_dir))

    assert mcp_server._doc_roots() == [str(cache_dir), str(vault_dir)]


def test_forbidden_message_names_the_remedy(text_pdf, tmp_path):
    """docs._prepare wraps paths.SourceForbidden with a document-specific
    remedy, the same way it already wraps SourceNotFound — the generic
    paths.py message alone never tells the agent what IS readable."""
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(docs.paths.SourceForbidden) as e:
        docs.extract(str(text_pdf), roots=[outside], cache_root=tmp_path)
    msg = str(e.value).lower()
    assert "uploads" in msg or "vault" in msg
    assert e.value.es_code == "doc_forbidden"


def test_forbidden_message_identical_whether_file_exists_or_not(text_pdf, tmp_path):
    """Confinement must fail the same way regardless of whether the forbidden
    path exists — otherwise the error becomes a path-probing oracle."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    # Both candidates sit outside the allowed root; one exists, one doesn't,
    # but they share a basename so the messages are comparable once that
    # shared name is substituted back in.
    existing = outside / "does-not-exist.pdf"
    existing.write_bytes(text_pdf.read_bytes())
    missing = outside / "does-not-exist-2.pdf"

    with pytest.raises(docs.paths.SourceForbidden) as e_exists:
        docs.extract(str(existing), roots=[allowed], cache_root=tmp_path)
    with pytest.raises(docs.paths.SourceForbidden) as e_missing:
        docs.extract(str(missing), roots=[allowed], cache_root=tmp_path)

    msg_exists = str(e_exists.value).replace(str(existing), "<PATH>")
    msg_missing = str(e_missing.value).replace(str(missing), "<PATH>")
    assert msg_exists == msg_missing
    assert "exist" not in msg_missing.lower()


# --- dispatch table ---------------------------------------------------------

def test_dispatch_table_covers_every_supported_extension():
    """SUPPORTED and the dispatch table must not drift apart — a format listed
    as supported with no converter would fail at call time, not import time."""
    from es.capabilities import docs
    assert set(docs.CONVERTERS) == docs.SUPPORTED


def test_unsupported_extension_names_the_supported_list(tmp_path):
    from es.capabilities import docs
    f = tmp_path / "x.rtf"
    f.write_text("x")
    with pytest.raises(docs.UnsupportedDocument) as e:
        docs.extract(str(f), roots=[tmp_path], cache_root=tmp_path)
    assert ".pdf" in str(e.value)


def test_extract_dispatches_a_csv_to_doc_text_with_no_page_count(csv_file, tmp_path):
    """End-to-end through docs.extract (not doc_text.convert directly): a
    flat format must report kind + doc_id like any other converter, but
    page_count stays None — that's the signal (see docs._page_count) that
    this format has no pages at all, not "zero" or "one"."""
    out = docs.extract(str(csv_file), roots=[csv_file.parent], cache_root=tmp_path)
    assert out["kind"] == "csv"
    assert out["page_count"] is None
    assert out["images"] == []
    assert "| Name | Position | Number |" in out["markdown"]


def test_extract_rejects_pages_argument_for_a_flat_format(csv_file, tmp_path):
    """`pages` presumes pagination; a flat format has none, so an explicit
    pages= is a loud InvalidPageRange, not a silent no-op."""
    with pytest.raises(docs.InvalidPageRange):
        docs.extract(str(csv_file), roots=[csv_file.parent], cache_root=tmp_path,
                     pages="1")


# --- non-PDF error mapping --------------------------------------------------

def test_corrupt_docx_maps_to_doc_unreadable(tmp_path):
    from es.capabilities import docs
    p = tmp_path / "bad.docx"
    p.write_bytes(b"not a zip")
    with pytest.raises(docs.UnreadableDocument):
        docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)


def test_corrupt_xlsx_maps_to_doc_unreadable(tmp_path):
    from es.capabilities import docs
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"not a zip")
    with pytest.raises(docs.UnreadableDocument):
        docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)


def test_render_rejects_a_non_pdf_with_a_clear_reason(csv_file, tmp_path):
    from es.capabilities import docs
    with pytest.raises(docs.UnsupportedDocument) as e:
        docs.render(str(csv_file), roots=[csv_file.parent], cache_root=tmp_path)
    assert "pdf" in str(e.value).lower()


def test_every_format_returns_the_stable_shape(
        csv_file, json_file, txt_file, ics_file, docx_file, xlsx_file, text_pdf, tmp_path):
    from es.capabilities import docs
    expected = {"doc_id", "kind", "page_count", "markdown", "images", "truncated"}
    for f in (csv_file, json_file, txt_file, ics_file, docx_file, xlsx_file, text_pdf):
        out = docs.extract(str(f), roots=[f.parent], cache_root=tmp_path)
        assert set(out) == expected, f.name
        assert out["markdown"].strip(), f.name
        assert out["kind"] == f.suffix.lstrip("."), f.name


def test_no_converter_leaks_a_raw_library_exception(tmp_path):
    """Coarse fuzz pass: every supported extension, fed garbage bytes, must
    produce an es_code from our catalogue — never a library's exception
    class name. A converter that happens to SUCCEED on this input also
    passes trivially; see
    test_realistic_malformed_documents_do_not_leak_a_raw_library_exception
    below for inputs that are guaranteed to actually fail, built from real
    library behavior rather than 30 bytes of noise."""
    from es.capabilities import docs
    allowed = {"doc_unreadable", "doc_encrypted", "doc_unsupported"}
    for ext in sorted(docs.SUPPORTED):
        p = tmp_path / f"garbage{ext}"
        p.write_bytes(b"\x00\x01\x02 not a real document \xff\xfe")
        try:
            docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
        except Exception as e:
            code = getattr(e, "es_code", type(e).__name__)
            assert code in allowed, f"{ext} leaked {code}: {e}"


# --- realistic malformed inputs must not leak a raw library exception -------
# (item 3) Each case here is a real, verified-empirically failure mode of
# python-docx/openpyxl/csv/json against an ordinary-mistake-shaped input (a
# renamed file, a partial download) — not adversarial fuzzing. Every one of
# these previously surfaced to the agent as the literal library exception
# class name (OSError, ValueError, ParseError, XMLSyntaxError, "Error",
# RecursionError, KeyError, AttributeError).

def _rezip_replacing(src_path, dest_path, filename, new_content):
    """Copy the zip at `src_path` to `dest_path`, replacing one member's
    bytes. Used to build a real .docx/.xlsx with one internal XML part
    corrupted/truncated/removed, which is what a partial download or a
    renamed-extension file actually looks like on disk — not something a
    from-scratch synthetic fixture can approximate."""
    import zipfile
    with zipfile.ZipFile(src_path) as zin, zipfile.ZipFile(dest_path, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == filename:
                if new_content is None:
                    continue  # drop this member entirely
                data = new_content
            zout.writestr(item, data)
    return dest_path


def test_realistic_malformed_documents_do_not_leak_a_raw_library_exception(tmp_path):
    from docx import Document
    from openpyxl import Workbook

    from es.capabilities import docs

    real_docx = tmp_path / "_real.docx"
    d = Document()
    d.add_paragraph("hello world, this is a real paragraph of real text")
    d.save(str(real_docx))

    real_xlsx = tmp_path / "_real.xlsx"
    wb = Workbook()
    wb.active.append(["a", "b"])
    wb.save(str(real_xlsx))

    cases = {}

    # 1. real .docx renamed .xlsx -> OSError (openpyxl)
    p = tmp_path / "docx_as_xlsx.xlsx"
    p.write_bytes(real_docx.read_bytes())
    cases["docx renamed .xlsx"] = p

    # 2. real .xlsx renamed .docx -> ValueError (python-docx)
    p = tmp_path / "xlsx_as_docx.docx"
    p.write_bytes(real_xlsx.read_bytes())
    cases["xlsx renamed .docx"] = p

    # 3. .xlsx with truncated sheet xml -> xml.etree.ElementTree.ParseError
    p = tmp_path / "truncated_sheet.xlsx"
    import zipfile
    with zipfile.ZipFile(real_xlsx) as zin:
        sheet = zin.read("xl/worksheets/sheet1.xml")
    _rezip_replacing(real_xlsx, p, "xl/worksheets/sheet1.xml", sheet[: len(sheet) // 2])
    cases[".xlsx truncated sheet xml"] = p

    # 4. .docx with truncated document.xml -> lxml.etree.XMLSyntaxError
    p = tmp_path / "truncated_document.docx"
    with zipfile.ZipFile(real_docx) as zin:
        document_xml = zin.read("word/document.xml")
    _rezip_replacing(real_docx, p, "word/document.xml", document_xml[: len(document_xml) // 2])
    cases[".docx truncated document.xml"] = p

    # 5. .csv with a field > the (raised) field-size limit
    p = tmp_path / "long_field.csv"
    p.write_text("a,b\n" + ("x" * (docs.CSV_FIELD_SIZE_LIMIT + 1000)) + ",y\n", encoding="utf-8")
    cases[".csv field over the size limit"] = p

    # 6. .csv with one unbalanced quote that swallows the rest of a large file
    #    into a single field past the size limit — same underlying csv.Error
    #    as case 5, different root cause.
    p = tmp_path / "unbalanced_quote.csv"
    p.write_text('a,b\n"' + ("y" * (docs.CSV_FIELD_SIZE_LIMIT + 1000)) + "\n", encoding="utf-8")
    cases[".csv unbalanced quote"] = p

    # 7. .json nested deeper than ~1000 levels -> RecursionError
    p = tmp_path / "deep.json"
    p.write_text("[" * 3000 + "]" * 3000, encoding="utf-8")
    cases[".json nested too deeply"] = p

    # 8. valid zip, no [Content_Types].xml -> KeyError
    p = tmp_path / "no_content_types.xlsx"
    _rezip_replacing(real_xlsx, p, "[Content_Types].xml", None)
    cases["valid zip, no [Content_Types].xml"] = p

    # 9. valid zip, [Content_Types].xml with no default namespace (python-docx
    #    never upgrades it to its own CT_Types wrapper class, so the next
    #    attribute access on it raises) -> AttributeError
    p = tmp_path / "empty_content_types.docx"
    _rezip_replacing(real_docx, p, "[Content_Types].xml", b"<?xml version='1.0'?><Types></Types>")
    cases["docx with a namespace-less [Content_Types].xml"] = p

    allowed = {"doc_unreadable", "doc_encrypted"}
    leaked_class_names = {
        "OSError", "ValueError", "ParseError", "XMLSyntaxError", "Error",
        "RecursionError", "KeyError", "AttributeError",
    }
    for label, path in cases.items():
        with pytest.raises(Exception) as e:
            docs.extract(str(path), roots=[tmp_path], cache_root=tmp_path)
        code = getattr(e.value, "es_code", type(e.value).__name__)
        assert code not in leaked_class_names, f"{label}: leaked raw {code}: {e.value}"
        assert code in allowed, f"{label}: unexpected es_code {code}: {e.value}"


# --- encrypted .docx / .xlsx -------------------------------------------------
# Neither python-docx nor openpyxl exposes a distinct "needs a password"
# exception type — a real password-protected .docx/.xlsx is stored as an
# OLE2/CFBF container (the same legacy container .doc/.xls used), never as a
# zip, so both libraries just fail to open it as a zip — indistinguishable
# by exception type alone from ordinary corruption (verified empirically
# against real python-docx/openpyxl behavior while building this feature).
# There is no dependency in this project (e.g. msoffcrypto-tool) to build a
# byte-perfect real encrypted Office document for a fixture, so these use the
# OLE2 magic-byte header alone — the same synthetic-bytes style already used
# by corrupt_pdf/empty_pdf above — which is sufficient to prove the detector
# actually fires, since those magic bytes are the entire signal it uses.

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def test_ole2_docx_is_reported_as_encrypted_not_generic_corruption(tmp_path):
    from es.capabilities import docs
    p = tmp_path / "protected.docx"
    p.write_bytes(_OLE2_MAGIC + b"\x00" * 500)
    with pytest.raises(docs.EncryptedDocument) as e:
        docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert "password" in str(e.value).lower()


def test_ole2_xlsx_is_reported_as_encrypted_not_generic_corruption(tmp_path):
    from es.capabilities import docs
    p = tmp_path / "protected.xlsx"
    p.write_bytes(_OLE2_MAGIC + b"\x00" * 500)
    with pytest.raises(docs.EncryptedDocument) as e:
        docs.extract(str(p), roots=[tmp_path], cache_root=tmp_path)
    assert "password" in str(e.value).lower()


# --- a bug in OUR OWN converter code must surface as itself, never get -----
# --- relabeled "corrupt file" (the whole point of this refactor) -----------
#
# _CONVERSION_ERRORS used to catch ValueError/KeyError/AttributeError over
# the ENTIRE mod.convert() call — not just the library's own open/parse
# step — so a real bug in doc_office.py/doc_text.py's own rendering logic
# that happened to raise one of those ordinary types would previously have
# been swallowed and misreported as doc_unreadable. These are regression
# guards for the fix: each monkeypatches a piece of a converter's OWN
# rendering logic (never the open/parse call itself) to raise, and asserts
# the raw exception propagates unchanged — proving docs.py's parse-error
# catch is scoped to the parse step, not the whole conversion.

def test_a_bug_in_doc_office_docx_rendering_is_not_masked_as_corrupt(
        docx_file, tmp_path, monkeypatch):
    from es.capabilities import docs, doc_office

    def boom(paragraph):
        raise ValueError("simulated bug in doc_office rendering")

    monkeypatch.setattr(doc_office, "_paragraph_block", boom)

    with pytest.raises(ValueError, match="simulated bug in doc_office rendering"):
        docs.extract(str(docx_file), roots=[docx_file.parent], cache_root=tmp_path)


def test_a_bug_in_doc_office_xlsx_rendering_is_not_masked_as_corrupt(
        xlsx_file, tmp_path, monkeypatch):
    from es.capabilities import docs, doc_office

    def boom(*args, **kwargs):
        raise ValueError("simulated bug in doc_office rendering")

    monkeypatch.setattr(doc_office, "_render_sheet_rows", boom)

    with pytest.raises(ValueError, match="simulated bug in doc_office rendering"):
        docs.extract(str(xlsx_file), roots=[xlsx_file.parent], cache_root=tmp_path)


def test_a_bug_in_doc_text_csv_rendering_is_not_masked_as_corrupt(
        csv_file, tmp_path, monkeypatch):
    from es.capabilities import docs, doc_text

    def boom(cells, width):
        raise ValueError("simulated bug in doc_text rendering")

    monkeypatch.setattr(doc_text, "format_row", boom)

    with pytest.raises(ValueError, match="simulated bug in doc_text rendering"):
        docs.extract(str(csv_file), roots=[csv_file.parent], cache_root=tmp_path)


def test_a_bug_in_doc_text_json_rendering_is_not_masked_as_corrupt(
        json_file, tmp_path, monkeypatch):
    """Same guard, but through the JSON handler's own truncation logic
    (rather than the parse step, which is json.loads/json.dumps — both
    inside the tight ParseFailed boundary, see doc_text._convert_json)."""
    from es.capabilities import docs, doc_text

    def boom(text, limit):
        raise ValueError("simulated bug in doc_text rendering")

    monkeypatch.setattr(doc_text, "_truncate_at_line_boundary", boom)

    with pytest.raises(ValueError, match="simulated bug in doc_text rendering"):
        docs.extract(str(json_file), roots=[json_file.parent], cache_root=tmp_path)


# --- cross-format cache collision (doc_id must include the format) ---------

def test_cross_format_cache_collision_is_fixed(text_pdf, tmp_path):
    """The exact repro from the review: a real PDF's bytes, saved once under
    a .csv extension and once under a .pdf extension, must NOT share a
    doc_id/artifact — each is read as its OWN format (a .csv reading of PDF
    bytes is garbage-but-real CSV output, not the PDF's actual content), so
    unifying their cache entries would silently hand back whichever format
    was converted first for the full 24h TTL."""
    from es.capabilities import docs
    content = text_pdf.read_bytes()
    as_csv = tmp_path / "same.csv"
    as_pdf = tmp_path / "same.pdf"
    as_csv.write_bytes(content)
    as_pdf.write_bytes(content)

    csv_out = docs.extract(str(as_csv), roots=[tmp_path], cache_root=tmp_path)
    pdf_out = docs.extract(str(as_pdf), roots=[tmp_path], cache_root=tmp_path)

    assert csv_out["doc_id"] != pdf_out["doc_id"]
    assert csv_out["kind"] == "csv"
    assert pdf_out["kind"] == "pdf"
    assert pdf_out["page_count"] == 2
    assert "Fall Season Schedule" in pdf_out["markdown"]
    assert "Fall Season Schedule" not in csv_out["markdown"]

    # Each format landed in its own artifact directory, keyed by its own id.
    assert (tmp_path / ".es" / csv_out["doc_id"] / "doc.md").is_file()
    assert (tmp_path / ".es" / pdf_out["doc_id"] / "doc.md").is_file()

    # Order independence: read .pdf first, THEN .csv, and re-check both are
    # still correct — the bug reproduced in either order.
    other_root = tmp_path / "reversed"
    other_root.mkdir()
    as_pdf2 = other_root / "same.pdf"
    as_csv2 = other_root / "same.csv"
    as_pdf2.write_bytes(content)
    as_csv2.write_bytes(content)
    pdf_out2 = docs.extract(str(as_pdf2), roots=[other_root], cache_root=tmp_path)
    csv_out2 = docs.extract(str(as_csv2), roots=[other_root], cache_root=tmp_path)
    assert pdf_out2["kind"] == "pdf" and "Fall Season Schedule" in pdf_out2["markdown"]
    assert csv_out2["kind"] == "csv" and "Fall Season Schedule" not in csv_out2["markdown"]


def test_cross_format_cache_collision_is_fixed_for_zero_byte_files(tmp_path):
    """Reproduced in the review specifically for zero-byte files too — an
    empty .csv and an empty .txt must not collide either."""
    from es.capabilities import docs
    as_csv = tmp_path / "empty.csv"
    as_txt = tmp_path / "empty.txt"
    as_csv.write_bytes(b"")
    as_txt.write_bytes(b"")

    csv_out = docs.extract(str(as_csv), roots=[tmp_path], cache_root=tmp_path)
    txt_out = docs.extract(str(as_txt), roots=[tmp_path], cache_root=tmp_path)
    assert csv_out["doc_id"] != txt_out["doc_id"]


def test_extract_same_extension_repeat_is_still_a_cache_hit(csv_file, tmp_path, monkeypatch):
    """Guard against the format-aware doc_id fix regressing the ORIGINAL
    cache-hit property for the common case: the same file, same extension,
    extracted twice must still convert only once."""
    from es.capabilities import docs
    calls = []
    original = docs.doc_text.convert

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(docs.doc_text, "convert", spy)

    first = docs.extract(str(csv_file), roots=[csv_file.parent], cache_root=tmp_path)
    second = docs.extract(str(csv_file), roots=[csv_file.parent], cache_root=tmp_path)

    assert len(calls) == 1
    assert first["doc_id"] == second["doc_id"]
    assert first["markdown"] == second["markdown"]
