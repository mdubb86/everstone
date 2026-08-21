"""PDF → Markdown, with image-only pages rasterized to sibling PNGs.

A page whose text layer carries fewer than BLANK_PAGE_CHARS characters is
image-only (a scan yields a little header noise, not literally nothing — so a
strict emptiness test would miss it). Those pages are rendered and linked
inline, which lets the agent hand the PNG to vision_analyze without needing to
know an escalation protocol.
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


def page_count(source: Path) -> int:
    with pdfplumber.open(source) as pdf:
        return len(pdf.pages)


def _table_to_markdown(table: List[List[Optional[str]]]) -> str:
    """Render one extracted table as a Markdown pipe table."""
    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in table if row]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, *body = rows
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join([" --- "] * width) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


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
    """Rasterize the given 1-indexed pages. Used by es_doc_render."""
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
            text = (page.extract_text() or "").strip()
            if len(text) < BLANK_PAGE_CHARS:
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
            parts.append(text)
            for table in page.extract_tables() or []:
                md = _table_to_markdown(table)
                if md:
                    parts.append(md)
    return "\n\n".join(parts), images
