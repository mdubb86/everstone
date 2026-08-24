"""PDF -> Markdown, with EVERY embedded raster image extracted as a sibling
PNG and linked inline at its position in the reading order.

There is no "is this image worth extracting" threshold. A prior version of
this module guessed at two thresholds — BLANK_PAGE_CHARS (auto-render a page
whose text layer looked empty) and MEANINGFUL_IMAGE_AREA_FRACTION (only
point the agent at es_doc_render for an image covering >=10% of the page) —
and both were wrong in the same way: they made "does the agent get to see
this image" a judgment call the CONVERTER made on the agent's behalf, one
the agent then had to notice and act on (a protocol step — the exact failure
mode this module exists to remove; see MEANINGFUL_IMAGE's old note, "use
es_doc_render", which the agent was free to just not do). The replacement
is not a better threshold, it is not having one: every embedded image is
extracted because it exists, not because it scored highly enough. A fully
scanned page (no text objects, one image spanning the page) falls out of
this for free — it needs no separate "is this page blank" detection, it is
just a page whose only content, text or image, happens to be one image.

Image discovery and rendering both go through pypdfium2's own page-object
model (`page.get_objects(filter=(pdfium.raw.FPDF_PAGEOBJ_IMAGE,))`), not
pdfplumber's `page.images`, and that choice is deliberate: pdfplumber is
still used for text/tables (pdfplumber has no per-object image rendering of
its own), but running two independent libraries' object models over the
same content stream and trusting them to agree 1:1 on which XObjects are
"the same image" is exactly the kind of fragile cross-library coupling this
module avoids elsewhere (see the historic note below on why the pdfplumber/
pypdfium2 overlap is kept minimal). Positioning an image against pdfplumber's
text/table blocks needs only ONE shared fact — where it sits vertically on
the page — and that is a simple, well-defined coordinate conversion
(pdfium's `get_bounds()` is PDF-native, y-up, from the page's bottom left;
pdfplumber's `top`/`bottom` are y-down, from the page's top-left; converting
one to the other is `page_height - y`), not an object-identity correlation.

CROP-RENDER VS STREAM EXTRACTION (investigated, not guessed):
pypdfium2's `PdfImage.get_bitmap(render=True, scale_to_original=True)`
renders ONE image object through pdfium's own renderer, with its placement
matrix applied and (per `scale_to_original`) upscaled back to the source
image's native pixel dimensions rather than the page's display DPI. This
beat both alternatives that were tried:

- Cropping a page raster (render the whole page at a fixed DPI via pdfium,
  then `PIL.crop()` the pixel box for that image's bbox) is simpler but
  ties image quality to a single page-wide DPI: a source image with a much
  higher native resolution than its on-page display size (confirmed with a
  1200x800 PNG placed at 150x100 points — a real, unremarkable case, not a
  contrived one) gets downsampled to whatever the page-wide DPI produces
  for that bbox, discarding real detail the file actually contains. Chasing
  that would mean computing a per-image ideal DPI and re-rendering the
  whole page at it — strictly more work than letting pdfium do this per
  OBJECT, which is what `scale_to_original=True` already does.
- Extracting the raw image stream (`img["stream"].get_data()` off
  pdfplumber's image dict) gets the undecoded source bytes at full native
  resolution, but ONLY the unrotated, unskewed pixels — verified directly:
  an image placed via `translate()` + `rotate(30)` reports the same raw
  bytes as an unrotated placement of the same source PNG, because the
  placement matrix is applied at PAINT time, not baked into the stream.
  Reproducing the visible page appearance from the raw stream would mean
  re-implementing the placement-matrix math (rotation, skew, scale) AND a
  PDF image decoder covering every colorspace/filter this module might see
  (DeviceGray/RGB/CMYK/Indexed, DCT/CCITTFax/JPX, soft masks) by hand —
  exactly the class of work a PDF rendering library already exists to do
  correctly. `get_bitmap(render=True, ...)` gets the rotated/skewed/masked
  appearance for free, verified the same way: bounds for that same rotated
  placement came back as an axis-aligned bounding box larger than the
  source's unrotated footprint (the correct AABB of a rotated rectangle),
  and the rendered bitmap's non-transparent pixels are the correctly
  rotated image, not the unrotated source.

So: crop-render at the OBJECT level (pdfium's own per-image renderer),
never a page-wide raster crop and never a raw stream. See
test_extracted_image_preserves_native_resolution_over_small_display and
test_extracted_image_pixel_color_matches_source.

MAX_IMAGE_DIMENSION bounds a single pathological embedded image (a real but
absurd case: a source image with a native resolution far beyond anything a
scan or photo would legitimately use, placed at any display size) from
producing an unbounded-size PNG — `scale_to_original=True` intentionally
chases native resolution with no ceiling of its own, so this module supplies
one. 4000px on the longest side is generous for any real scan or photo (a
600 DPI scan of a full US Letter page is 5100x6600 at its largest dimension
uncropped; a single embedded image is smaller than the whole page) while
still bounding a crafted or synthetic outlier. This is a RESOURCE ceiling,
the same kind as MAX_EXTRACTED_IMAGES below, not a quality judgment.

MAX_EXTRACTED_IMAGES bounds the document as a whole: an ordinary large
document (a 200-page scanned book, one image per page) must convert in
full, but a pathological one (a PDF crafted or corrupted to carry far more
embedded images than any real document would) must not be allowed to write
an unbounded number of PNGs into the cache. 500 sits comfortably above the
200-page-scan case (2.5x headroom for an unusually long real scan) while
still being a real, finite bound. Hitting it is reported IN-BAND, once per
page that has skipped images, naming the count and pointing at
es_doc_render (still the escape hatch for reaching an over-ceiling image
today; deleting that tool is a later phase) — never a silent drop. See
test_image_extraction_ceiling_is_enforced_and_reported_in_band.

Precondition: `adir` must already exist for both `convert()` and `render()`
— callers create it via `doc_cache.artifact_dir`; this module doesn't own
that.
"""
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from es import doc_cache
from es.capabilities.doc_support import table_to_markdown as _table_to_markdown

RENDER_DPI = 150

# Used ONLY by docs.render() (es_doc_render) now, to cap how many PAGES an
# explicit render request may rasterize in one call — NOT an auto-render
# concept any more (that branch, and the "blank page" detection that used
# to trigger it, is gone; see the module docstring). Kept under this name
# because docs.py reads it directly (`MAX_RENDER_PAGES =
# doc_pdf.MAX_AUTO_RENDER_PAGES`) as the one place that limit is owned.
MAX_AUTO_RENDER_PAGES = 20

# Resource ceilings for embedded-image extraction — see the module
# docstring for the reasoning behind each number.
MAX_EXTRACTED_IMAGES = 500
MAX_IMAGE_DIMENSION = 4000


def page_count(source: Path) -> int:
    with pdfplumber.open(source) as pdf:
        return len(pdf.pages)


def _render_page(doc: "pdfium.PdfDocument", adir: Path, page_no: int) -> Path:
    """Rasterize one 1-indexed page to a PNG in `adir`, using an already-open
    pdfium document. Used by render()/es_doc_render — a WHOLE-page raster,
    unrelated to the per-embedded-image extraction convert() does (see
    _page_image_entries).

    Takes the open document rather than a source path: opening a fresh
    pypdfium2.PdfDocument per page (once per page of a multi-page render) is
    both wasteful — re-parsing the whole file's object table and resources
    each time — and the kind of repeated open/close-of-the-same-file churn
    that pypdfium2's own docs warn is best avoided. Callers open one document
    for the whole render and close it once when done.
    """
    page = doc[page_no - 1]
    try:
        bitmap = page.render(scale=RENDER_DPI / 72)
        out = doc_cache.page_image_path(adir, page_no)
        bitmap.to_pil().save(out)
        return out
    finally:
        page.close()


def render(source: Path, adir: Path, pages: List[int]) -> List[Path]:
    """Rasterize the given 1-indexed pages. Used by es_doc_render.

    Raises ValueError if any requested page number is outside the document's
    valid range — the caller (docs.parse_pages) validates first in practice,
    but this function is public and must not surface a raw pdfium error.
    """
    total = page_count(source)
    for n in pages:
        if not (1 <= n <= total):
            raise ValueError(
                f"page {n} is out of range: this document has {total} "
                f"page{'s' if total != 1 else ''} (valid pages: 1-{total})")
    with pdfium.PdfDocument(str(source)) as doc:
        return [_render_page(doc, adir, n) for n in pages]


def _text_and_table_entries(page, tables) -> List[Tuple[float, str, str]]:
    """One page's prose lines and tables, as (top, kind, content) entries in
    the page's own vertical (top-down, points-from-page-top) coordinate
    space — kind is "line" or "table". Table regions are excluded from the
    text extraction (outside_bbox) so a table's cell content isn't ALSO
    emitted as prose (the same duplication `_prose_text` used to guard
    against). Lines, not one prose blob, so a caller can interleave images
    between individual lines rather than only before/after one giant text
    block — see _assemble_page's docstring for why that granularity matters.
    """
    bboxes = [t.bbox for t in tables]
    text_page = page
    for bbox in bboxes:
        text_page = text_page.outside_bbox(bbox)
    entries: List[Tuple[float, str, str]] = []
    for line in text_page.extract_text_lines() or []:
        text = (line.get("text") or "").strip()
        if text:
            entries.append((line["top"], "line", text))
    for table, bbox in zip(tables, bboxes):
        md = _table_to_markdown(table.extract())
        if md:
            entries.append((bbox[1], "table", md))
    return entries


def _page_image_entries(pf_page, page_height: float, adir: Path, page_no: int,
                         budget_remaining: int) -> Tuple[List[Tuple[float, str, str]], List[Path], int]:
    """Every embedded image on one page, as (top, kind, content) entries in
    the SAME top-down coordinate space _text_and_table_entries uses (see the
    module docstring's note on converting pdfium's y-up `get_bounds()`),
    plus the list of PNGs actually written and how many of this page's
    images counted against the document-wide MAX_EXTRACTED_IMAGES budget.

    `budget_remaining` is how many MORE images this whole document is still
    allowed to extract (MAX_EXTRACTED_IMAGES minus what earlier pages on
    this same convert() call already used) — once it's spent, every further
    image on this page becomes a single consolidated "not extracted" note
    (not one note per image, and never a silent drop) rather than another
    PNG.
    """
    entries: List[Tuple[float, str, str]] = []
    saved: List[Path] = []
    extracted = 0
    skipped = 0
    first_skipped_top: Optional[float] = None
    image_no = 0
    for obj in pf_page.get_objects(filter=(pdfium_raw.FPDF_PAGEOBJ_IMAGE,)):
        image_no += 1
        _left, bottom, _right, top = obj.get_bounds()
        top_td = page_height - top
        if extracted >= budget_remaining:
            skipped += 1
            if first_skipped_top is None:
                first_skipped_top = top_td
            continue
        bitmap = obj.get_bitmap(render=True, scale_to_original=True)
        try:
            pil = bitmap.to_pil()
        finally:
            bitmap.close()
        if max(pil.size) > MAX_IMAGE_DIMENSION:
            pil.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
        out = doc_cache.page_image_path(adir, page_no, image_no)
        pil.save(out)
        saved.append(out)
        extracted += 1
        entries.append((top_td, "image", f"![page {page_no} image {image_no}]({out})"))
    if skipped:
        entries.append((first_skipped_top, "note",
            f"*(page {page_no} also contains {skipped} further "
            f"image{'s' if skipped != 1 else ''} not extracted — the "
            f"document-wide limit of {MAX_EXTRACTED_IMAGES} images was "
            f"reached; use es_doc_render with pages=\"{page_no}\" to view "
            "this page.)*"))
    return entries, saved, extracted


def _assemble_page(entries: List[Tuple[float, str, str]]) -> List[str]:
    """entries are (top, kind, content) from BOTH _text_and_table_entries
    and _page_image_entries, unsorted. Sorted here by vertical position,
    then adjacent "line" entries are merged into one paragraph (so ordinary
    prose isn't emitted one Markdown block per line) while every "table"/
    "image"/"note" entry stays a standalone block exactly where its position
    places it.

    This is what makes an image's link land AT the image's position in the
    reading order — interleaved with the surrounding text — rather than
    appended after everything on the page, the same problem doc_office
    solves by walking a .docx body in document order instead of appending
    every table after all paragraphs (see doc_office's module docstring).
    A table also participates in this same ordering (not just images): the
    old code always emitted a page's tables after its prose, unconditionally
    — now a table between two paragraphs sorts between them too, for the
    same reason an image does.
    """
    entries = sorted(entries, key=lambda e: e[0])
    parts: List[str] = []
    buf: List[str] = []
    for _top, kind, content in entries:
        if kind == "line":
            buf.append(content)
            continue
        if buf:
            parts.append("\n".join(buf))
            buf = []
        parts.append(content)
    if buf:
        parts.append("\n".join(buf))
    return parts


def convert(source: Path, adir: Path,
            pages: Optional[List[int]] = None) -> Tuple[str, List[Path]]:
    """Return (markdown, extracted_image_paths).

    pdfplumber (text/tables) and pypdfium2 (rendering embedded images) are
    two independent libraries reading the same file — unavoidable, since
    pdfplumber has no per-object image renderer of its own — but the
    pypdfium2 document is opened at most ONCE per call, up front, and reused
    for every requested page (never re-opened per page or per image).
    """
    parts: List[str] = []
    images: List[Path] = []
    extracted_images = 0
    with pdfplumber.open(source) as pdf, pdfium.PdfDocument(str(source)) as pdfium_doc:
        for idx, page in enumerate(pdf.pages, start=1):
            if pages is not None and idx not in pages:
                continue
            parts.append(f"## Page {idx}")

            tables = page.find_tables()
            entries = _text_and_table_entries(page, tables)

            pf_page = pdfium_doc[idx - 1]
            try:
                img_entries, saved, count = _page_image_entries(
                    pf_page, page.height, adir, idx,
                    MAX_EXTRACTED_IMAGES - extracted_images)
            finally:
                pf_page.close()
            entries.extend(img_entries)
            images.extend(saved)
            extracted_images += count

            parts.extend(_assemble_page(entries))
    return "\n\n".join(parts), images
