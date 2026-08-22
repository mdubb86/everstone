"""Document reading: confine the path, dispatch on format, cache the result.

The only module the MCP layer imports for es_doc_*. Keeps doc_pdf free of
cache and confinement concerns, and doc_cache free of parsing concerns.

Dispatch is a table, CONVERTERS, keyed by lowercased extension; SUPPORTED is
derived from it so the two can't drift apart. CONVERTERS holds ".pdf" (via
doc_pdf, the reference/paginated converter), ".txt"/".md"/".csv"/".json"
(all four via doc_text, the flat/non-paginated converter), ".ics" (via
doc_ics, which synthesizes one "## " heading per VEVENT so a flat calendar
feed still pages well), and ".docx"/".xlsx" (both via doc_office, which reads
a Word document's own heading outline and gives each Excel sheet its own "##"
heading — neither has page_count/render, same as doc_text's formats) —
adding a new format is meant to be a pure addition (a new converter module +
one new table entry), not a change to extract()/render() themselves.
"""
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from pdfminer.pdfdocument import PDFEncryptionError
from pdfplumber.utils.exceptions import PdfminerException

from es import config, doc_cache, paths
from es.capabilities import doc_ics, doc_office, doc_pdf, doc_text

MAX_MARKDOWN_CHARS = 40_000
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
# Mirrors doc_pdf.MAX_AUTO_RENDER_PAGES rather than duplicating the number —
# render() and extract()'s auto-render both bound the same disk-fill risk
# (rasterizing into Hermes's shared upload cache), so one call site owning the
# limit and the other pointing at it keeps them from drifting apart.
MAX_RENDER_PAGES = doc_pdf.MAX_AUTO_RENDER_PAGES

# Maps a supported extension to its converter module. Each converter is
# expected to implement a shared per-format contract (doc_pdf.py is the
# reference): `convert(source, adir, pages=None) -> (markdown, images)` is
# REQUIRED; `page_count(source) -> int` and `render(source, adir, pages) ->
# images` are OPTIONAL — their presence is how the rest of this module tells
# a paginated/renderable format (PDF, via doc_pdf) apart from a flat one
# (txt/md/csv/json, via doc_text — none of the four have pages, so that
# module implements neither attribute) without hardcoding a format list
# anywhere else. SUPPORTED is DERIVED from this table rather than
# hand-maintained alongside it, so the two can never drift apart: an entry
# here is automatically "supported", and nothing can claim to be supported
# without a converter.
CONVERTERS = {
    ".pdf": doc_pdf,
    ".txt": doc_text,
    ".md": doc_text,
    ".csv": doc_text,
    ".json": doc_text,
    ".ics": doc_ics,
    ".docx": doc_office,
    ".xlsx": doc_office,
}
SUPPORTED = set(CONVERTERS)

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


def _page_count(mod, real: Path) -> Optional[int]:
    """None means "this format has no concept of pages" (a flat format like
    .csv/.txt/.json/.md, all served by doc_text) — kept a distinct value
    from 0 or 1:

    - NOT 0: for a format that DOES paginate, _page_count already treats a
      report of zero pages as an error below (a PDF that opens but claims no
      pages is corrupt/empty, not legitimately "paginated with nothing" — an
      indistinguishable value here would erase that distinction and make a
      flat format look broken).
    - NOT 1: a page count of 1 would suggest page-range/render semantics
      apply ("page 1 of 1"), which is false for something that isn't
      paginated at all.

    The dispatch signal is simply whether the converter module exposes
    `page_count` — a flat converter that never implements it is, by
    construction, a format with no pages.

    The exception mapping below (PdfminerException -> the PDF-specific
    encrypted/unreadable errors) is deliberately PDF-specific and today only
    ever fires for doc_pdf (the only converter with a page_count). A future
    paginated converter with its own failure modes gets its own mapping at
    its own call site — this function does not try to generalize that part
    ahead of need.
    """
    if not hasattr(mod, "page_count"):
        return None
    try:
        total = mod.page_count(real)
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


_VAULT_PREFIX = "$vault/"


def _expand_source(source: str) -> str:
    """Rewrite the two accepted vault-relative `source` forms into a path
    string for paths.resolve_readable to confine — expansion never
    substitutes for confinement, it only decides what candidate string gets
    checked next:

    - an absolute path: returned untouched. Checked FIRST and unconditionally,
      so an absolute path is never reinterpreted as vault-relative. This is
      also how every Telegram upload arrives (Hermes injects an absolute
      path), so it stays the primary form for uploads — there is no
      cache-relative form to expand.
    - "$vault/..." (the prefix must include the slash, so a literal file
      named "$vault" with no trailing slash is never treated as the prefix —
      it just falls through to the bare-relative case below): an explicit,
      unambiguous synonym for the vault-relative form. Looked up by name here
      (docs.py owns "which root is 'the vault'"); paths.py never learns that
      name.
    - anything else (a bare relative path): joined onto the vault root — the
      same convention es_notes_read/es_notes_attach/es_notes_list already use
      (they hand back vault-relative paths like "Topics/Soccer/schedule.pdf"
      for exactly this source), so "$vault/X" and bare "X" are interchangeable.

    A "$vault/../../etc/passwd" (or bare "../../etc/passwd") traversal is NOT
    rejected here — Path joining does not normalize ".." — it is rejected by
    resolve_readable() next, exactly the same way any other out-of-root
    absolute path is, because that call site resolves (normalizes) the path
    THEN checks containment. Expansion only changes what string reaches that
    check, never whether the check runs.
    """
    s = str(source)
    if os.path.isabs(s):
        return s
    if s.startswith(_VAULT_PREFIX):
        return str(config.vault_root() / s[len(_VAULT_PREFIX):])
    return str(config.vault_root() / s)


def _prepare(source: str, roots, cache_root: Path):
    """Shared front half: purge, expand, confine, size-check, dispatch-check.

    Confinement runs before the size/extension checks below, but AFTER the
    stale-artifact purge — the purge only ever touches our own `.es/`
    namespace under `cache_root` (see doc_cache.purge), never the candidate
    `source` path, so an unauthorized caller cannot use it to affect anything
    outside the cache they already have no special claim over. Ordering the
    purge first just means every call — authorized or not — keeps the cache
    tidy; it does not weaken confinement.
    """
    doc_cache.purge(cache_root)
    expanded = _expand_source(source)
    try:
        real = paths.resolve_readable(expanded, roots)
    except paths.SourceNotFound as e:
        # paths.py is deliberately generic; this is where the document-domain
        # remedy belongs. Uploaded files are cache-evicted after 24h, and the
        # agent has no other way to learn that — so name the fix, not just
        # the symptom.
        raise paths.SourceNotFound(
            f"{e} — if this was a recently uploaded file, it may have aged "
            "out of the cache (uploads are only kept for 24 hours); ask the "
            "user to resend it") from e
    except paths.SourceForbidden as e:
        # Same reasoning as SourceNotFound above: paths.py stays generic (and
        # — critically — identical whether or not the path exists, closing a
        # probing oracle), so the document-specific remedy is added here, not
        # there. The remedy MUST NOT depend on anything paths.py doesn't
        # already expose (existence, real target, ...) or the oracle reopens
        # — everything appended below is static text plus the untouched `{e}`,
        # never anything that differs between an existing and a missing path.
        # Naming the accepted forms here (not just "uploads or the vault") is
        # the actual fix for the bug this feature exists for: a vault-relative
        # source used to land here — doc_forbidden — for what was really a
        # path-FORM mistake, not an authorization one.
        raise paths.SourceForbidden(
            f"{e} — source must be an absolute path inside the vault or "
            "uploads, \"$vault/...\", or a vault-relative path (e.g. "
            "\"Topics/Manual.pdf\"); ask the user to resend it as a Telegram "
            "upload, or save it into the vault first") from e
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
    """None means "treat as a cache MISS" — both for a plain absence and for
    an unreadable/undecodable doc.md. doc_id is a CONTENT hash, so a doc.md
    truncated by a crash or ENOSPC would otherwise poison every future
    extract of this exact document for the full 24h TTL (identical content ->
    identical doc_id -> the same broken cache entry every time). Reconverting
    and overwriting on a bad read is strictly better than raising — mirrors
    how _read_images_manifest already treats a broken images.json."""
    md_path = adir / DOC_MD_NAME
    if not md_path.is_file():
        return None
    try:
        return md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


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


# Matches the "\n\n" that doc_pdf.convert's "\n\n".join(parts) puts before
# every page's "## Page N" heading except the very first — i.e. every safe
# block-boundary cut point in the markdown.
_PAGE_BOUNDARY_RE = re.compile(r"\n\n(?=## Page (\d+)\b)")
# The very first heading in the markdown — for a page-SUBSET extract (e.g.
# pages="5-8") this is NOT page 1, so the no-earlier-boundary fallback must
# read the real number here rather than assume 1.
_FIRST_PAGE_RE = re.compile(r"\A## Page (\d+)\b")
# A markdown image link as doc_pdf.convert emits it: "![page N](/path.png)".
# Used only to keep a HARD (no-boundary-available) cut from landing inside
# one — the boundary cut above never can, since links are their own block.
_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _safe_hard_cut(text: str, limit: int) -> int:
    """A cut index <= limit that never lands inside a markdown image link —
    landing inside one would hand the agent a truncated, unusable path."""
    cut = min(limit, len(text))
    for m in _IMAGE_LINK_RE.finditer(text):
        if m.start() < cut < m.end():
            return m.start()
    return cut


def _truncate_markdown(markdown: str, total_pages: int) -> Tuple[str, bool]:
    """Cut `markdown` to at most MAX_MARKDOWN_CHARS at a page-block boundary
    (the last "## Page N" heading before the limit), never mid-word or
    mid-image-link, and append a marker naming where extraction stopped and
    how to resume. Returns (markdown, truncated).

    If even the FIRST page's content alone exceeds the limit there is no
    earlier block boundary to cut at — a page-range resume can't help (the
    identical oversized page would just come back truncated the same way
    again), so that case falls back to a hard cut (still guarded against
    landing inside an image link) and says plainly why a resume marker isn't
    offered, rather than pretending a narrower range would fix anything.
    Reads the real page number off the first heading rather than assuming 1
    — a page-SUBSET extract (e.g. pages="5-8") starts with "## Page 5", not 1.
    """
    if len(markdown) <= MAX_MARKDOWN_CHARS:
        return markdown, False

    boundaries = [(m.start(), int(m.group(1)))
                  for m in _PAGE_BOUNDARY_RE.finditer(markdown)]
    candidates = [b for b in boundaries if b[0] <= MAX_MARKDOWN_CHARS]

    if candidates:
        cut_pos, next_page = max(candidates)
        stopped_after = next_page - 1
        marker = (f"\n\n*(truncated after page {stopped_after} of {total_pages} "
                   f"— call es_doc_extract again with pages=\"{stopped_after + 1}-"
                   f"{total_pages}\" to continue)*")
        return markdown[:cut_pos] + marker, True

    first_match = _FIRST_PAGE_RE.match(markdown)
    first_page = int(first_match.group(1)) if first_match else 1
    cut_pos = _safe_hard_cut(markdown, MAX_MARKDOWN_CHARS)
    marker = (f"\n\n*(truncated inside page {first_page} — its content alone "
              f"exceeds the {MAX_MARKDOWN_CHARS}-character limit, so there is "
              "no earlier page boundary to stop at; narrowing pages won't "
              f"help since page {first_page} alone is already too large — try "
              "es_doc_render on this page instead)*")
    return markdown[:cut_pos] + marker, True


def extract(source: str, roots, cache_root: Path,
            pages: Optional[str] = None) -> dict:
    real, did, adir = _prepare(source, roots, cache_root)
    ext = real.suffix.lower()
    mod = CONVERTERS[ext]
    total = _page_count(mod, real)

    # `pages` on a format with no pages (total is None) is a loud error, not
    # a silent no-op — same philosophy as render()'s explicit-out-of-range
    # behavior below: an argument that cannot mean anything for this
    # document is more likely a mistaken assumption about its format than an
    # intentional "give me everything" request (which is already spelled by
    # omitting pages entirely).
    if pages is not None and total is None:
        raise InvalidPageRange(
            f"{ext} documents do not have pages — omit the pages argument "
            "to extract the whole document")
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
                markdown, image_paths = mod.convert(real, adir, pages=None)
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
            markdown, image_paths = mod.convert(real, adir, pages=wanted)
        except PdfminerException as e:
            _reraise_pdf_error(real, e)
        doc_cache.touch(adir)
        images = [str(i) for i in image_paths]

    markdown, truncated = _truncate_markdown(markdown, total)
    # kind is trivially the extension without its dot ("pdf" for ".pdf") —
    # correct today and requires no per-format table of its own.
    return {"doc_id": did, "kind": ext.lstrip("."), "page_count": total,
            "markdown": markdown, "images": images, "truncated": truncated}


DEFAULT_RENDER_PAGES_HI = 10


def render(source: str, roots, cache_root: Path, pages: Optional[str] = None) -> dict:
    """pages=None means "no explicit request" — render the first
    DEFAULT_RENDER_PAGES_HI pages, clamped down to the document's actual
    length so a short document (the 1-3 page schedule this feature exists
    for) doesn't error just because it's shorter than the default window.

    An EXPLICIT pages argument is never clamped: if the agent asks for a
    range that runs past the document's end, that's a loud error (same as
    extract()'s explicit-range behavior) rather than a silent partial
    result — an explicit out-of-range ask is more likely a wrong page
    number than an intentional "give me what you can" request.
    """
    real, did, adir = _prepare(source, roots, cache_root)
    ext = real.suffix.lower()
    mod = CONVERTERS[ext]
    # Rasterizing pages only means something for a format that HAS pages to
    # rasterize — a converter without `render` (every flat format: txt/md/
    # csv/json, all via doc_text) can't support this call at all, independent
    # of whatever `pages` was passed, so the capability check comes first and
    # names the actual reason rather than failing confusingly deeper down
    # (e.g. inside parse_pages against a page_count that doesn't exist).
    # hasattr is enough: it mirrors the same "presence of the optional
    # attribute IS the capability" convention _page_count already uses for
    # page_count, so a reader only has to learn the pattern once.
    if not hasattr(mod, "render"):
        raise UnsupportedDocument(
            f"{ext} cannot be rendered to images — es_doc_render can only "
            "render pages of a PDF; try es_doc_extract instead")
    total = _page_count(mod, real)
    spec = pages if pages is not None else f"1-{min(DEFAULT_RENDER_PAGES_HI, total)}"
    wanted = parse_pages(spec, total)
    if len(wanted) > MAX_RENDER_PAGES:
        raise InvalidPageRange(
            f"cannot render {len(wanted)} pages in one call — the limit is "
            f"{MAX_RENDER_PAGES}; narrow the range (or call es_doc_render "
            "again for the rest)")
    images = mod.render(real, adir, wanted)
    doc_cache.touch(adir)
    return {"doc_id": did, "page_count": total,
            "images": [str(i) for i in images]}
