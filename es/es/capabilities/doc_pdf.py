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
es_doc_extract's image_pages parameter (the escape hatch for reaching an
over-ceiling image — see docs.extract) — never a silent drop. See
test_image_extraction_ceiling_is_enforced_and_reported_in_band.

Precondition: `adir` must already exist for both `convert()` and `render()`
— callers create it via `doc_cache.artifact_dir`; this module doesn't own
that.

VECTOR DRAWINGS (charts, diagrams — paths, not raster images):
A chart drawn with lines/curves/filled shapes (a bar chart, a plotted line,
a diagram) is neither text nor an embedded raster image — pdfplumber's text
extraction gives only axis labels, and Task 1's embedded-image extraction
(above) finds nothing, because there is no XObject to find. Left alone, a
page like that produces markdown reading "Revenue 0 50 100 Q1 Q2 Q3" with no
picture — the chart is invisible. `_page_drawing_entries` closes this by
rasterizing clusters of `page.lines + page.rects + page.curves`.

Subtraction first: a bordered table's own grid lines are ALSO
lines/rects — measured directly (see the fixtures for
test_bordered_table_produces_no_drawing_image / table_and_chart_pdf), every
one of a bordered table's border lines falls inside `find_tables()`'s bbox.
Without subtracting those out before clustering, every table would be
doubled: once correctly as a Markdown table, and again as a spurious
"drawing" of its own borders. The subtraction is a simple bbox-containment
test (a vector object counts as "the table's" if its bbox lies inside a
detected table's bbox, within a small float tolerance) — not a judgment
about the object's content, purely where it sits.

Clustering: proximity/gap-based, not "everything remaining on the page is
one drawing." The simpler whole-page rule is wrong for a page with two
unrelated charts (verified with two_charts_side_by_side_pdf) — it would
crop one image spanning both charts AND the blank gap between them, rather
than two separate, tightly-cropped drawings. The rule implemented
(`_cluster_boxes`) is connected-components over the remaining objects'
bboxes: two objects join the same cluster if the gap between their bboxes
is <= DRAWING_CLUSTER_GAP in BOTH axes (an overlap counts as a negative gap,
so overlapping/touching objects always merge); clusters are transitive
(A-B-C merges into one group even if A and C aren't directly close), which
is what lets a chart's separate bars all merge with its axis lines even
though no two of them share an edge. DRAWING_CLUSTER_GAP = 36pt (0.5in):
comfortably larger than the spacing BETWEEN a chart's own elements (bars,
tick marks — tens of points at most in every fixture measured) and
comfortably smaller than the whitespace gap real layouts put BETWEEN
unrelated elements (a column gutter, or the ~100-250pt measured between two
side-by-side charts in the test fixture).

The size floor (MIN_DRAWING_DIMENSION = 20pt, ~0.28in/7mm) rejects a
CLUSTER (not a single raw object — an axis line is legitimately
zero-height, but the chart it belongs to isn't) whose bbox is smaller than
20pt in EITHER dimension. This is a geometric claim, not an importance
judgment: measured directly, a hairline rule/underline/separator's bbox has
one dimension at or near 0 (a horizontal line's height, a vertical line's
width — see lone_rule_pdf), while every real chart/diagram fixture measured
is 150-300pt in both dimensions. 20pt sits well above realistic stroke
widths (0-3pt) and well below the smallest real drawing observed, with
headroom in both directions.

The floor applies to the CLUSTER's bbox, never to individual objects within
it, and this was checked against real documents, not assumed: on
example_paper.pdf (tests/fixtures/pdf/, a real academic paper), the actual
bars of a real 42-bar chart are only 7.7pt WIDE each (measured directly —
more categories on a similarly-sized chart than icml_numpapers.pdf's
16.2pt-wide bars). A rule requiring each individual object to itself clear
the floor in both dimensions would reject that chart outright — a false
NEGATIVE on real data, worse than the false positives below. Only the
cluster's union bbox is measured against the floor.

10pt (not 20pt) was also tried, prompted by a first pass at real per-object
measurements suggesting real prose pages' vector objects are all "thin"
(<10pt in one dimension). Checked directly against colm2025_conference.pdf
and example_paper.pdf: two clusters that are NOT chart content — a
booktabs-style table's header-rule pair ("PART / DESCRIPTION", 205.9pt wide
x 16.1pt tall) and an algorithm-box title bar ("Algorithm 1 Bubble Sort",
234pt wide x 13.5pt tall) — both clear a 10pt floor and both were confirmed
by rendering the crop (not guessed) to be pure decorative rule pairs, not
drawings. 20pt rejects both (13.5 and 16.1 are under 20) while still
passing every real chart measured. This is why 20pt, not 10pt.

KNOWN FALSE POSITIVE, not fixed: a filled decorative shape (or hollow
frame) big enough in both dimensions to clear the floor is not
geometrically distinguishable from real chart content — there is no size-
or shape-only rule that tells them apart, and adding a content-aware one
(e.g. "does the interior have any non-blank pixels") would be exactly the
kind of importance/content judgment this whole module avoids, and would
risk rejecting real charts that are mostly white space around thin lines.
Measured on realistic_report_page_pdf (header rule + footer rule + bordered
table + one shading band): the rules and table borders are fully
suppressed (subtraction + floor), and the shading band alone survives as
one false-positive drawing — see test_realistic_page_false_positive_count.
The same shape (an isolated, floor-clearing cluster with no chart content)
occurs on a REAL document too: colm2025_conference.pdf page 3 has a hollow
rectangle (4 hairline segments, no fill) that survives as one drawing.
Rendered and inspected directly (not assumed): it is the LaTeX template's
literal "Figure 1: Sample figure caption" placeholder — an intentionally
blank, captioned figure region. That makes it arguably not even a false
positive: a blank box is what that part of the page actually looks like.
See test_real_paper_with_no_charts_produces_only_the_known_figure_placeholder,
which pins this at exactly one drawing (not zero) and documents why.

COST: measured, not assumed. `_cluster_boxes` is O(n^2) in the number of
NON-table vector objects remaining on one page (pairwise proximity checks,
then union-find) — isolated directly: 1000 scattered non-merging boxes
cluster in ~160ms, 2000 in ~650ms. That sounds alarming until placed next
to what real pages actually leave behind: a 50-page document of dense
bordered tables (41 lines/page, ALL subtracted, nothing left to cluster)
costs the same with drawing detection on or off (1.70s vs 1.74s for 50
pages — the difference is noise, not signal) because `page.lines`/
`page.rects`/`page.curves` are pdfplumber-cached properties already
computed internally by `find_tables()` for the SAME page, so this module
pays ~nothing extra to read them. 50 synthetic pages each with a small real
chart (a handful of objects per page) convert in 0.43s total, against a
0.07s no-vector-content baseline for the same page count — a per-page cost
similar in order of magnitude to Task 1's per-image cost. On real documents
(tests/fixtures/pdf/): colm2025_conference.pdf (5pp, 18 vector objects
total) converts in ~0.21s, example_paper.pdf (7pp, 133 vector objects, one
page with 61 after table subtraction) in ~0.42s, icml_numpapers.pdf (1pp,
113 objects, a real chart) in ~0.04s. The O(n^2) term only matters for a
PATHOLOGICAL page — hundreds to thousands of small, mutually distant,
non-table vector objects on one page — which no fixture measured here
produces; this module accepts that as a known, undocumented-further
scaling edge rather than adding a second, harder-to-justify ceiling on
object count (MAX_EXTRACTED_DRAWINGS bounds OUTPUT, not this computation).

MAX_EXTRACTED_DRAWINGS bounds drawings document-wide, SEPARATELY from
MAX_EXTRACTED_IMAGES — the two are different resources produced by
different code paths (a pdfium per-object bitmap fetch vs. a pdfplumber
page-region crop-to-image) with different per-item cost, and a document
heavy in one shouldn't starve budget from the other (a photo-heavy scanned
report and a chart-heavy financial report are different documents; sharing
one counter would make an unrelated resource's abundance the reason a later
chart gets silently downgraded to a note). 100 is smaller than
MAX_EXTRACTED_IMAGES's 500 because real documents legitimately contain
dozens to hundreds of embedded photos (one per scanned page) but rarely
more than a handful of true vector charts per document — 100 is generous
headroom above any real report this module has been measured against, while
still a finite, real bound. Hitting it is reported IN-BAND (mirroring
MAX_EXTRACTED_IMAGES exactly) — never a silent drop.
"""
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from es import doc_cache
from es.capabilities.doc_support import table_to_markdown as _table_to_markdown

RENDER_DPI = 150

# Used ONLY by docs.extract()'s image_pages parameter, to cap how many PAGES
# one explicit image_pages request may rasterize in a single call — not an
# auto-render concept (there is no default page range; image_pages is opt-in
# and only ever renders exactly the pages named). docs.py reads it directly
# (`MAX_IMAGE_PAGES = doc_pdf.MAX_IMAGE_PAGES`) as the one place this limit
# is owned.
MAX_IMAGE_PAGES = 20

# Resource ceilings for embedded-image extraction — see the module
# docstring for the reasoning behind each number.
MAX_EXTRACTED_IMAGES = 500
MAX_IMAGE_DIMENSION = 4000

# Vector-drawing (chart/diagram) extraction — see the module docstring's
# "VECTOR DRAWINGS" section for the reasoning behind each of these.
MIN_DRAWING_DIMENSION = 20  # points; a cluster smaller than this in either
                            # dimension is a rule/underline, not a drawing.
DRAWING_CLUSTER_GAP = 36    # points; objects this close or closer merge
                            # into one drawing.
MAX_EXTRACTED_DRAWINGS = 100  # document-wide ceiling, separate from
                               # MAX_EXTRACTED_IMAGES — see the docstring.


def page_count(source: Path) -> int:
    with pdfplumber.open(source) as pdf:
        return len(pdf.pages)


def _render_page(doc: "pdfium.PdfDocument", adir: Path, page_no: int) -> Path:
    """Rasterize one 1-indexed page to a PNG in `adir`, using an already-open
    pdfium document. Used by render(), which docs.extract() calls when its
    image_pages parameter is given — a WHOLE-page raster, unrelated to the
    per-embedded-image extraction convert() does (see _page_image_entries).

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
    """Rasterize the given 1-indexed pages. Called by docs.extract() when its
    image_pages parameter is given.

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
            f"reached; call es_doc_extract with image_pages=\"{page_no}\" to "
            "see this page as an image.)*"))
    return entries, saved, extracted


def _bbox_of(obj) -> Tuple[float, float, float, float]:
    return (obj["x0"], obj["top"], obj["x1"], obj["bottom"])


def _inside_any_table(bbox: Tuple[float, float, float, float],
                       table_bboxes: List[Tuple[float, float, float, float]],
                       tol: float = 1.0) -> bool:
    """True if `bbox` lies inside (within float tolerance `tol`) any of
    `table_bboxes` — the subtraction step: a bordered table's own grid
    lines/rects must not also be counted as drawing material. See the
    module docstring's "VECTOR DRAWINGS" section for the measurement behind
    this (every one of a bordered table's border lines falls inside its
    find_tables() bbox)."""
    x0, top, x1, bottom = bbox
    for tx0, ttop, tx1, tbottom in table_bboxes:
        if (x0 >= tx0 - tol and x1 <= tx1 + tol and
                top >= ttop - tol and bottom <= tbottom + tol):
            return True
    return False


def _non_table_drawing_boxes(page, tables) -> List[Tuple[float, float, float, float]]:
    """Every line/rect/curve bbox on the page that is NOT part of a detected
    table — the raw candidate material for drawing clustering."""
    table_bboxes = [t.bbox for t in tables]
    boxes: List[Tuple[float, float, float, float]] = []
    for obj in list(page.lines) + list(page.rects) + list(page.curves):
        bbox = _bbox_of(obj)
        if not _inside_any_table(bbox, table_bboxes):
            boxes.append(bbox)
    return boxes


def _boxes_close(a: Tuple[float, float, float, float],
                  b: Tuple[float, float, float, float], gap: float) -> bool:
    """True if two bboxes overlap or are within `gap` points of each other
    in BOTH axes. An overlapping/touching pair has a negative or zero gap in
    that axis, which always satisfies "<= gap" — so this only actually
    separates two objects when they are far apart in at least one
    dimension, matching how real layouts separate unrelated elements
    (mostly horizontally, for side-by-side charts; mostly vertically, for
    stacked sections)."""
    ax0, atop, ax1, abottom = a
    bx0, btop, bx1, bbottom = b
    h_gap = max(ax0, bx0) - min(ax1, bx1)
    v_gap = max(atop, btop) - min(abottom, bbottom)
    return h_gap <= gap and v_gap <= gap


def _cluster_boxes(boxes: List[Tuple[float, float, float, float]],
                    gap: float) -> List[Tuple[float, float, float, float]]:
    """Connected-components clustering of bboxes by proximity — see the
    module docstring's "VECTOR DRAWINGS" section for why proximity
    clustering (not "everything on the page is one drawing") is the rule,
    and why 36pt is the gap. Transitive: A close to B and B close to C
    merges all three into one cluster even if A and C are not directly
    close, which is what lets every bar/axis-line of one chart merge into a
    single drawing without needing a smarter per-pair rule."""
    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _boxes_close(boxes[i], boxes[j], gap):
                union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters = []
    for idxs in groups.values():
        x0 = min(boxes[i][0] for i in idxs)
        top = min(boxes[i][1] for i in idxs)
        x1 = max(boxes[i][2] for i in idxs)
        bottom = max(boxes[i][3] for i in idxs)
        clusters.append((x0, top, x1, bottom))
    return clusters


def _page_drawing_entries(page, tables, adir: Path, page_no: int,
                           budget_remaining: int) -> Tuple[List[Tuple[float, str, str]], List[Path], int]:
    """Every vector-drawing cluster on one page, as (top, kind, content)
    entries in pdfplumber's own top-down coordinate space (no y-flip needed
    here — unlike _page_image_entries, everything in this function comes
    from pdfplumber, not pdfium), plus the PNGs written and how many of this
    page's drawings counted against the document-wide MAX_EXTRACTED_DRAWINGS
    budget. Mirrors _page_image_entries's budget/reporting shape exactly —
    see its docstring for the "consolidated note, never a silent drop" logic
    this reuses.

    Clusters are sorted into reading order (top, then left-to-right) before
    numbering, so drawing_no and the interleaved entry order are both
    deterministic regardless of the order pdfplumber returns lines/rects/
    curves in.
    """
    boxes = _non_table_drawing_boxes(page, tables)
    clusters = _cluster_boxes(boxes, DRAWING_CLUSTER_GAP)
    clusters = [c for c in clusters
                if (c[2] - c[0]) >= MIN_DRAWING_DIMENSION
                and (c[3] - c[1]) >= MIN_DRAWING_DIMENSION]
    clusters.sort(key=lambda c: (c[1], c[0]))

    entries: List[Tuple[float, str, str]] = []
    saved: List[Path] = []
    extracted = 0
    skipped = 0
    first_skipped_top: Optional[float] = None
    drawing_no = 0
    for x0, top, x1, bottom in clusters:
        drawing_no += 1
        if extracted >= budget_remaining:
            skipped += 1
            if first_skipped_top is None:
                first_skipped_top = top
            continue
        pad = 2.0
        crop_bbox = (max(x0 - pad, 0.0), max(top - pad, 0.0),
                     min(x1 + pad, page.width), min(bottom + pad, page.height))
        image = page.crop(crop_bbox).to_image(resolution=RENDER_DPI)
        out = doc_cache.page_drawing_path(adir, page_no, drawing_no)
        image.original.save(out)
        saved.append(out)
        extracted += 1
        entries.append((top, "drawing", f"![page {page_no} drawing {drawing_no}]({out})"))
    if skipped:
        entries.append((first_skipped_top, "note",
            f"*(page {page_no} also contains {skipped} further vector "
            f"drawing{'s' if skipped != 1 else ''} not extracted — the "
            f"document-wide limit of {MAX_EXTRACTED_DRAWINGS} drawings was "
            f"reached; call es_doc_extract with image_pages=\"{page_no}\" to "
            "see this page as an image.)*"))
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
    extracted_drawings = 0
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

            draw_entries, draw_saved, draw_count = _page_drawing_entries(
                page, tables, adir, idx,
                MAX_EXTRACTED_DRAWINGS - extracted_drawings)
            entries.extend(draw_entries)
            images.extend(draw_saved)
            extracted_drawings += draw_count

            parts.extend(_assemble_page(entries))
    return "\n\n".join(parts), images
