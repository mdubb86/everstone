"""Document reading: confine the path, dispatch on format, cache the result.

The only module the MCP layer imports for es_doc_*. Keeps doc_pdf free of
cache and confinement concerns, and doc_cache free of parsing concerns.
"""
from pathlib import Path
from typing import List, Optional

from es import doc_cache, paths
from es.capabilities import doc_pdf

MAX_MARKDOWN_CHARS = 40_000
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
SUPPORTED = {".pdf"}


class UnsupportedDocument(Exception):
    es_code = "doc_unsupported"


class DocumentTooLarge(Exception):
    es_code = "doc_too_large"


class InvalidPageRange(Exception):
    es_code = "doc_invalid_pages"


def parse_pages(spec: str, page_count: int) -> List[int]:
    """Parse "1-3,7" into a sorted, deduplicated, 1-indexed page list."""
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-", 1))
            else:
                lo = hi = int(part)
        except ValueError:
            raise InvalidPageRange(
                f"could not read page range {spec!r} — use forms like "
                '"3", "1-5", or "1-2,7"') from None
        if lo < 1 or hi > page_count or lo > hi:
            raise InvalidPageRange(
                f"pages {part!r} are outside this document (1-{page_count})")
        out.update(range(lo, hi + 1))
    if not out:
        raise InvalidPageRange(f"no pages selected by {spec!r}")
    return sorted(out)


def _prepare(source: str, roots, cache_root: Path):
    """Shared front half: purge, confine, size-check, dispatch-check.

    Confinement runs before the size/extension checks below, but AFTER the
    stale-artifact purge — the purge only ever touches our own `.es/`
    namespace under `cache_root` (see doc_cache.purge), never the candidate
    `source` path, so an unauthorized caller cannot use it to affect anything
    outside the cache they already have no special claim over. Ordering the
    purge first just means every call — authorized or not — keeps the cache
    tidy; it does not weaken confinement.
    """
    doc_cache.purge(cache_root)
    try:
        real = paths.resolve_readable(source, roots)
    except paths.SourceNotFound as e:
        # paths.py is deliberately generic; this is where the document-domain
        # remedy belongs. Uploaded files are cache-evicted after 24h, and the
        # agent has no other way to learn that — so name the fix, not just
        # the symptom.
        raise paths.SourceNotFound(
            f"{e} — if this was a recently uploaded file, it may have aged "
            "out of the cache (uploads are only kept for 24 hours); ask the "
            "user to resend it") from e
    ext = real.suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedDocument(
            f"cannot read {ext or 'files without an extension'} — "
            f"supported: {', '.join(sorted(SUPPORTED))}")
    if real.stat().st_size > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge(
            f"{real.name} is larger than the {MAX_DOCUMENT_BYTES // (1024*1024)}MB "
            "limit; ask for a smaller file or a page range")
    did = doc_cache.doc_id(real)
    adir = doc_cache.artifact_dir(cache_root, did)
    return real, did, adir


def extract(source: str, roots, cache_root: Path,
            pages: Optional[str] = None) -> dict:
    real, did, adir = _prepare(source, roots, cache_root)
    total = doc_pdf.page_count(real)
    wanted = parse_pages(pages, total) if pages else None
    markdown, images = doc_pdf.convert(real, adir, pages=wanted)
    (adir / "doc.md").write_text(markdown)
    doc_cache.touch(adir)
    truncated = len(markdown) > MAX_MARKDOWN_CHARS
    if truncated:
        markdown = markdown[:MAX_MARKDOWN_CHARS]
    return {"doc_id": did, "kind": "pdf", "page_count": total,
            "markdown": markdown, "images": [str(i) for i in images],
            "truncated": truncated}


def render(source: str, roots, cache_root: Path, pages: str = "1-10") -> dict:
    real, did, adir = _prepare(source, roots, cache_root)
    total = doc_pdf.page_count(real)
    wanted = parse_pages(pages, total)
    images = doc_pdf.render(real, adir, wanted)
    doc_cache.touch(adir)
    return {"doc_id": did, "page_count": total,
            "images": [str(i) for i in images]}
