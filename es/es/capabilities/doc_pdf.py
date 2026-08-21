"""PDF → Markdown, with image-only pages rasterized to sibling PNGs.

A page whose text layer carries fewer than BLANK_PAGE_CHARS characters is
image-only (a scan yields a little header noise, not literally nothing — so a
strict emptiness test would miss it). Those pages are rendered and linked
inline, which lets the agent hand the PNG to vision_analyze without needing to
know an escalation protocol.

The blank-page decision is made on the page's FULL, unfiltered text (tables
included), not on the table-excluded prose text computed for display. A page
that is nothing but one big table has full text (its cell content) that is
comfortably non-blank, even though its non-table prose is empty — using the
filtered text for the blank check would misclassify that page as an image and
rasterize it instead of rendering its table.

Precondition: `adir` must already exist for both `convert()` and `render()` —
callers create it via `doc_cache.artifact_dir`; this module doesn't own that.
"""
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber
import pypdfium2 as pdfium

from es import doc_cache

# Matches Hermes's own tuned constant (tools/read_extract.py).
BLANK_PAGE_CHARS = 20
RENDER_DPI = 150
MAX_AUTO_RENDER_PAGES = 20

# A page image below this fraction of the page area is treated as decoration
# (a letterhead logo, a rule line) and passes silently. An image at or above
# it is treated as meaningful content the agent would otherwise miss (a
# chart, a form, a map) and earns a note pointing at es_doc_render. 10% is
# comfortably above what a logo/rule occupies (well under 1% of a page) and
# comfortably below a half-page chart or scanned figure (20%+).
MEANINGFUL_IMAGE_AREA_FRACTION = 0.10


def page_count(source: Path) -> int:
    with pdfplumber.open(source) as pdf:
        return len(pdf.pages)


def _table_to_markdown(table: List[List[Optional[str]]]) -> str:
    """Render one extracted table as a Markdown pipe table.

    Cell text is escaped so a literal '|' can't be mistaken for a column
    separator, which would misalign every following row.
    """
    def cell(c: Optional[str]) -> str:
        return (c or "").replace("\n", " ").strip().replace("|", "\\|")

    rows = [[cell(c) for c in row] for row in table if row]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, *body = rows
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join([" --- "] * width) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _prose_text(page, table_bboxes: List[Tuple[float, float, float, float]]) -> str:
    """Page text with the given table bounding boxes excluded, so table
    content isn't emitted twice — once as prose, once as a pipe table."""
    if not table_bboxes:
        return (page.extract_text() or "").strip()
    prose_page = page
    for bbox in table_bboxes:
        prose_page = prose_page.outside_bbox(bbox)
    return (prose_page.extract_text() or "").strip()


def _has_meaningful_image(page) -> bool:
    page_area = page.width * page.height
    if page_area <= 0:
        return False
    return any(
        (img["width"] * img["height"]) / page_area >= MEANINGFUL_IMAGE_AREA_FRACTION
        for img in page.images
    )


def _render_page(source: Path, adir: Path, page_no: int) -> Path:
    """Rasterize one 1-indexed page to a PNG in `adir`."""
    doc = pdfium.PdfDocument(str(source))
    try:
        page = doc[page_no - 1]
        bitmap = page.render(scale=RENDER_DPI / 72)
        out = doc_cache.page_image_path(adir, page_no)
        bitmap.to_pil().save(out)
        return out
    finally:
        doc.close()


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
    return [_render_page(source, adir, n) for n in pages]


def convert(source: Path, adir: Path,
            pages: Optional[List[int]] = None) -> Tuple[str, List[Path]]:
    """Return (markdown, rendered_image_paths)."""
    parts: List[str] = []
    images: List[Path] = []
    rendered = 0
    with pdfplumber.open(source) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            if pages is not None and idx not in pages:
                continue
            parts.append(f"## Page {idx}")
            full_text = (page.extract_text() or "").strip()

            if len(full_text) < BLANK_PAGE_CHARS:
                if rendered < MAX_AUTO_RENDER_PAGES:
                    img = _render_page(source, adir, idx)
                    images.append(img)
                    rendered += 1
                    parts.append(f"![page {idx}]({img})")
                else:
                    parts.append(
                        f"*(page {idx} is an image; not rendered — the per-call "
                        f"limit of {MAX_AUTO_RENDER_PAGES} pages was reached. "
                        f"Use es_doc_render with pages=\"{idx}\".)*")
                continue

            tables = page.find_tables()
            bboxes = [t.bbox for t in tables]
            prose = _prose_text(page, bboxes)
            if prose:
                parts.append(prose)
            for table in page.extract_tables() or []:
                md = _table_to_markdown(table)
                if md:
                    parts.append(md)

            if _has_meaningful_image(page):
                parts.append(
                    f"*(page {idx} also contains an image not shown here — "
                    f"use es_doc_render with pages=\"{idx}\" to view it.)*")
    return "\n\n".join(parts), images
