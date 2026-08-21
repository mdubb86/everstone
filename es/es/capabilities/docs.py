"""Document reading: confine the path, dispatch on format, cache the result.

The only module the MCP layer imports for es_doc_*. Keeps doc_pdf free of
cache and confinement concerns, and doc_cache free of parsing concerns.

Today this only understands PDFs — there is no real format dispatch yet
(`kind` is hardcoded "pdf"). Dispatch on extension/content-type arrives with
Phase 2's other document formats; SUPPORTED is the seam that will grow.
"""
import json
from pathlib import Path
from typing import List, Optional

from pdfminer.pdfdocument import PDFEncryptionError
from pdfplumber.utils.exceptions import PdfminerException

from es import doc_cache, paths
from es.capabilities import doc_pdf

MAX_MARKDOWN_CHARS = 40_000
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
# Mirrors doc_pdf.MAX_AUTO_RENDER_PAGES rather than duplicating the number —
# render() and extract()'s auto-render both bound the same disk-fill risk
# (rasterizing into Hermes's shared upload cache), so one call site owning the
# limit and the other pointing at it keeps them from drifting apart.
MAX_RENDER_PAGES = doc_pdf.MAX_AUTO_RENDER_PAGES
SUPPORTED = {".pdf"}

# Cache filenames within a doc_id's artifact dir. Both are written ONLY for a
# full-document extract (pages=None) — see the comment in extract().
DOC_MD_NAME = "doc.md"
DOC_IMAGES_MANIFEST = "images.json"


class UnsupportedDocument(Exception):
    es_code = "doc_unsupported"


class DocumentTooLarge(Exception):
    es_code = "doc_too_large"


class InvalidPageRange(Exception):
    es_code = "doc_invalid_pages"


class EncryptedDocument(Exception):
    es_code = "doc_encrypted"


class UnreadableDocument(Exception):
    es_code = "doc_unreadable"


def parse_pages(spec: str, page_count: int) -> List[int]:
    """Parse "1-3,7" into a sorted, deduplicated, 1-indexed page list."""
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo_s, hi_s = part.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
            else:
                lo = hi = int(part)
        except ValueError:
            raise InvalidPageRange(
                f"could not read page range {spec!r} — use forms like "
                '"3", "1-5", or "1-2,7"') from None
        if lo < 1 or hi < 1:
            raise InvalidPageRange(
                f"page range {part!r} must use page numbers starting at 1")
        if lo > hi:
            raise InvalidPageRange(
                f'page range {part!r} is reversed — did you mean "{hi}-{lo}"?')
        if page_count <= 0:
            raise InvalidPageRange(
                f"pages {part!r} are outside this document (it has no pages)")
        if hi > page_count:
            raise InvalidPageRange(
                f"pages {part!r} are outside this document (1-{page_count})")
        out.update(range(lo, hi + 1))
    if not out:
        raise InvalidPageRange(f"no pages selected by {spec!r}")
    return sorted(out)


def _reraise_pdf_error(real: Path, exc: PdfminerException):
    """pdfplumber wraps every parse failure in ONE exception class
    (PdfminerException), passing the real pdfminer exception as its sole arg
    — that is the only place "needs a password" is distinguishable from
    "not a readable PDF at all" (the encrypted case otherwise surfaces with
    an EMPTY message, telling the agent nothing). Verified empirically:
    pdfminer.pdfdocument.PDFPasswordIncorrect (raised for both a missing and
    a wrong password) is a subclass of PDFEncryptionError, so one isinstance
    check reliably separates the two — no guessing required."""
    cause = exc.args[0] if exc.args else None
    if isinstance(cause, PDFEncryptionError):
        raise EncryptedDocument(
            f"{real.name} is password-protected — es cannot open encrypted "
            "PDFs; ask the user for an unlocked copy") from exc
    raise UnreadableDocument(
        f"{real.name} could not be read as a PDF — it may be corrupt, "
        "truncated, or not actually a PDF; ask the user to resend it") from exc


def _page_count(real: Path) -> int:
    try:
        total = doc_pdf.page_count(real)
    except PdfminerException as e:
        _reraise_pdf_error(real, e)
    if total <= 0:
        # Opens fine but has no pages — pdfplumber does not consider this an
        # error, but a real document always has at least one page, and a
        # silent empty result is indistinguishable from "genuinely blank" for
        # the agent. Treat it as the same class of problem as corruption.
        raise UnreadableDocument(
            f"{real.name} has no readable pages — it may be corrupt or "
            "empty; ask the user to resend it")
    return total


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
    except OSError as e:
        # resolve_readable's own existence check can still hit the filesystem
        # AFTER confinement already passed (e.g. ENAMETOOLONG on a path that
        # resolves lexically but blows past PATH_MAX/NAME_MAX). That is an
        # operational failure inside an already-allowed root, not a
        # confinement bypass — map it to a domain error instead of leaking a
        # raw OSError (and the full offending path) to the agent.
        raise UnreadableDocument(
            "that path is too long or otherwise unusable on this "
            "filesystem; ask the user to resend the file") from e

    ext = real.suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedDocument(
            f"cannot read {ext or 'files without an extension'} — "
            f"supported: {', '.join(sorted(SUPPORTED))}")
    try:
        size = real.stat().st_size
    except OSError as e:
        raise UnreadableDocument(
            "could not read this file from disk; ask the user to resend it"
        ) from e
    if size > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge(
            f"{real.name} is larger than the {MAX_DOCUMENT_BYTES // (1024*1024)}MB "
            "limit; ask for a smaller file or a page range")
    did = doc_cache.doc_id(real)
    adir = doc_cache.artifact_dir(cache_root, did)
    return real, did, adir


def _read_cached_markdown(adir: Path) -> Optional[str]:
    md_path = adir / DOC_MD_NAME
    if not md_path.is_file():
        return None
    return md_path.read_text(encoding="utf-8")


def _read_images_manifest(adir: Path) -> List[str]:
    manifest = adir / DOC_IMAGES_MANIFEST
    if not manifest.is_file():
        return []
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_full_extract(adir: Path, markdown: str, images: List[Path]) -> None:
    (adir / DOC_MD_NAME).write_text(markdown, encoding="utf-8")
    (adir / DOC_IMAGES_MANIFEST).write_text(
        json.dumps([str(i) for i in images]), encoding="utf-8")


def extract(source: str, roots, cache_root: Path,
            pages: Optional[str] = None) -> dict:
    real, did, adir = _prepare(source, roots, cache_root)
    total = _page_count(real)
    wanted = parse_pages(pages, total) if pages is not None else None

    if wanted is None:
        # Full-document extract: doc_id is a content hash, so a previous
        # conversion of this exact content is still correct — check the
        # cache before paying for another convert().
        cached = _read_cached_markdown(adir)
        if cached is not None:
            doc_cache.touch(adir)  # TTL means "24h since last USE", not
                                    # "24h since conversion" — a cache hit is
                                    # a use.
            markdown = cached
            images = _read_images_manifest(adir)
        else:
            try:
                markdown, image_paths = doc_pdf.convert(real, adir, pages=None)
            except PdfminerException as e:
                _reraise_pdf_error(real, e)
            _write_full_extract(adir, markdown, image_paths)
            doc_cache.touch(adir)
            images = [str(i) for i in image_paths]
    else:
        # A page-SUBSET extract is never written to doc.md/images.json: those
        # two files are the whole-document artifact that a future es_read
        # will address as `doc:<id>` (Phase 2). Writing a subset there would
        # silently replace the full document with a fragment for every
        # future reader of this doc_id — worse than not caching at all. So
        # subsets always convert fresh and are simply never persisted; only
        # the full-document result is cached.
        try:
            markdown, image_paths = doc_pdf.convert(real, adir, pages=wanted)
        except PdfminerException as e:
            _reraise_pdf_error(real, e)
        doc_cache.touch(adir)
        images = [str(i) for i in image_paths]

    truncated = len(markdown) > MAX_MARKDOWN_CHARS
    if truncated:
        markdown = markdown[:MAX_MARKDOWN_CHARS]
    return {"doc_id": did, "kind": "pdf", "page_count": total,
            "markdown": markdown, "images": images, "truncated": truncated}


def render(source: str, roots, cache_root: Path, pages: str = "1-10") -> dict:
    real, did, adir = _prepare(source, roots, cache_root)
    total = _page_count(real)
    wanted = parse_pages(pages, total)
    if len(wanted) > MAX_RENDER_PAGES:
        raise InvalidPageRange(
            f"cannot render {len(wanted)} pages in one call — the limit is "
            f"{MAX_RENDER_PAGES}; narrow the range (or call es_doc_render "
            "again for the rest)")
    images = doc_pdf.render(real, adir, wanted)
    doc_cache.touch(adir)
    return {"doc_id": did, "page_count": total,
            "images": [str(i) for i in images]}
