"""Cache layout for converted documents.

Artifacts live in a `.es/<doc_id>/` subdirectory of Hermes's own document
cache. We reuse Hermes's directory but own our namespace: its cleanup
(`_cleanup_cache_dir`) iterates files only and never recurses, so it will
never touch ours — and ours never touches its inbound files.
"""
import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Optional

DOC_ID_LEN = 12
ES_NAMESPACE = ".es"
TTL_SECONDS = 24 * 3600


def doc_id(source: Path, ext: Optional[str] = None) -> str:
    """Content-AND-format hash of the source file. Stable for the same input,
    so re-extracting the same document (same bytes, same format) is a cache
    hit rather than a re-conversion.

    `ext` folds the format into the identity, not just the file's bytes —
    deliberately, not merely for convenience. With eight converters keyed by
    extension, identical bytes read as two different formats (a real PDF
    that happens to have once been uploaded and read as `.csv`) produce two
    entirely different, independently CORRECT documents: different `kind`,
    different markdown, different page_count. Those are not "the same
    document read twice" — collapsing them onto one id caches whichever
    format got extracted first and silently returns that garbage for every
    later request of the other format, for the full 24h TTL (the bug this
    fixes). And since `doc_id` is handed back to the agent and is meant to
    become an addressable handle (`doc:<id>`, a later phase), two
    representations of the same bytes SHOULD be different ids: the agent's
    mental model is "one id = one document I can address", and a `.csv`
    reading and a `.pdf` reading of the same bytes are two different
    documents by any definition that matters to it (different content,
    different kind, different affordances — a PDF has pages/render, a CSV
    doesn't). Folding the extension into the HASH (rather than nesting the
    artifact directory by extension under an unchanged content-only id) keeps
    that "one id = one document" property literal instead of merely
    structural, and needs no change to `artifact_dir`/`page_image_path`/the
    rest of this module's directory layout.

    Defaults to `source.suffix.lower()` when not given explicitly, so every
    existing caller/test that only ever hashes one extension per Path
    continues to work unchanged.
    """
    if ext is None:
        ext = source.suffix.lower()
    h = hashlib.sha256()
    h.update(ext.encode("utf-8"))
    h.update(b"\0")
    with open(source, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:DOC_ID_LEN]


def artifact_dir(cache_root: Path, did: str) -> Path:
    """The per-document artifact directory, created if absent."""
    d = Path(cache_root) / ES_NAMESPACE / did
    d.mkdir(parents=True, exist_ok=True)
    return d


def page_image_path(adir: Path, page_no: int, image_no: Optional[int] = None) -> Path:
    """Zero-padded so lexical order matches page order (and, when given,
    image order within a page).

    `image_no` defaults to None for the WHOLE-PAGE raster es_doc_render
    produces (`pNNN.png` — one file per requested page, unchanged from
    before this parameter existed). Passing `image_no` names one of
    POSSIBLY SEVERAL embedded images extracted from a single page
    (`pNNN-iMM.png`) — doc_pdf.convert() now extracts every embedded raster
    image on a page, not just one, so "one PNG per page" is no longer a safe
    assumption for that path. The two forms can never collide: only the
    `-iMM` suffix distinguishes an embedded-image crop from a whole-page
    render, and a page's whole-page render (`pNNN.png`) is only ever written
    by a SEPARATE call (es_doc_render), never alongside convert()'s own
    per-image files for the same page.
    """
    if image_no is None:
        return Path(adir) / f"p{page_no:03d}.png"
    return Path(adir) / f"p{page_no:03d}-i{image_no:02d}.png"


def page_drawing_path(adir: Path, page_no: int, drawing_no: int) -> Path:
    """A rasterized VECTOR drawing (a clustered group of lines/rects/curves —
    a chart, diagram, or similar — cropped and rendered from a page, as
    opposed to an embedded raster image lifted out whole). `-dMM`, distinct
    from both `page_image_path`'s bare `pNNN.png` (whole-page render) and its
    `-iMM` (embedded image) forms, so all three can coexist in the same
    artifact directory for the same page without ever colliding: a page can
    legitimately have an embedded photo AND a vector chart, and each needs
    its own file."""
    return Path(adir) / f"p{page_no:03d}-d{drawing_no:02d}.png"


def office_image_path(adir: Path, image_no: int, ext: str) -> Path:
    """One embedded image extracted from a `.docx` (see doc_office.py).

    A `.docx` has no page concept the way a PDF does, so there is no `pNNN`
    to key off of — this is numbered by a single flat RUNNING INDEX across
    the whole document, in extraction (document) order, zero-padded so
    lexical order matches that order the same way `pNNN`/`pNNN-iMM` do for a
    PDF.

    `ext` preserves the image part's OWN on-disk extension (png/jpeg/gif/
    ...) rather than forcing a PNG re-encode: a `.docx` package stores the
    original image FILE, unlike a PDF page (which has no single "original
    file" a rendered/rotated embedded image was ever decoded from at its
    on-page appearance) — extracting those bytes unchanged is both cheaper
    and higher-fidelity than any re-render, so the file this writes is
    literally the same bytes the package already contains and should keep
    that file's own extension. See doc_office.py's module docstring for the
    measurement behind this choice.
    """
    return Path(adir) / f"img{image_no:03d}.{ext}"


def touch(adir: Path) -> None:
    """Mark a document as used. Makes the directory mtime an ACCESS time, so
    the TTL means '24h since last use' rather than '24h since conversion' —
    otherwise a document expires while a conversation is still using it."""
    now = time.time()
    try:
        os.utime(adir, (now, now))
    except OSError:
        pass


def purge(cache_root: Path, ttl_seconds: int = TTL_SECONDS) -> int:
    """Delete artifact directories unused for longer than the TTL. Returns the
    count removed. Only touches our `.es/` namespace — Hermes's own inbound
    files in the parent directory are left alone."""
    ns = Path(cache_root) / ES_NAMESPACE
    if not ns.is_dir():
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for d in ns.iterdir():
        try:
            if d.is_symlink():
                # Nothing here creates symlinks (artifact_dir only mkdirs), and
                # rmtree refuses to act through one — so counting it as removed
                # would overstate the result. Skip rather than lie.
                continue
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            # Removed concurrently between iterdir() and stat() — not our problem.
            continue
    return removed
