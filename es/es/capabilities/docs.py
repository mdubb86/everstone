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
one new table entry), not a change to extract() itself.
"""
import csv
import json
import os
from pathlib import Path
from typing import List, Optional

from pdfminer.pdfdocument import PDFEncryptionError
from pdfplumber.utils.exceptions import PdfminerException

from es import config, doc_cache, paths
from es.capabilities import doc_ics, doc_office, doc_pdf, doc_support, doc_text

# extract() no longer returns the document — it returns a RECEIPT: a handle
# plus just enough text (`preview`) for the agent to tell what it's holding
# before deciding whether to page through the rest via es_read. 800 is sized
# for that identification job (a title, a first paragraph, a table header),
# not for reading — es_read (paging the full doc.md this module still
# caches in full) is the only path meant to return enough to actually read.
PREVIEW_CHARS = 800
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
# Mirrors doc_pdf.MAX_IMAGE_PAGES rather than duplicating the number — both
# bound the same disk-fill risk (rasterizing into Hermes's shared upload
# cache) for the same one call site (extract()'s image_pages), so one module
# owning the limit and the other pointing at it keeps them from drifting
# apart.
MAX_IMAGE_PAGES = doc_pdf.MAX_IMAGE_PAGES

# The stdlib csv module's default field_size_limit (128 KiB) is a parser
# safety valve, not a real document-shape limit — a single free-text CSV
# column (a pasted comment, a description field) can legitimately exceed it,
# and hitting the default raises `_csv.Error: field larger than field limit`,
# which used to surface to the agent as the literal, unexplainable es_code
# "Error". csv.field_size_limit() is a process-wide setting on the `_csv` C
# module, not a per-Reader option, so it can be raised HERE — once, for the
# life of the process — without doc_text.py (which owns the actual
# `csv.reader()` call) needing to change at all. 10 MiB comfortably covers any
# realistic single field while still being a real, bounded ceiling: a CSV
# with an unbalanced/unterminated quote makes csv.reader treat everything
# from that quote to EOF as ONE field (see doc_text._convert_csv's docstring
# on why it must use a real line iterator, not splitlines()), so an
# unterminated quote in a large file must still hit SOME limit rather than
# buffer the rest of a 50MB upload into one Python string.
CSV_FIELD_SIZE_LIMIT = 10 * 1024 * 1024
csv.field_size_limit(CSV_FIELD_SIZE_LIMIT)

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

# Cache filenames within a doc_id's artifact dir. All three are written ONLY
# for a full-document extract (pages=None) — see the comment in extract().
DOC_MD_NAME = "doc.md"
DOC_IMAGES_MANIFEST = "images.json"
# The sidecar reader.py's `doc:<id>` resolution reads to learn a handle's
# `kind` without re-deriving it from a source file it no longer has (only
# the doc_id, a one-way content hash, survives past extract() — the original
# extension is not recoverable from it). A small JSON sidecar beside doc.md
# was chosen over inventing a second cache (or folding kind into the doc_id
# string itself, which would mean changing DOC_ID_LEN/_DOC_ID_RE and every
# caller that treats a doc_id as opaque hex) because it's the same pattern
# images.json already established: one small JSON file per artifact,
# written once at conversion time, read by read_cached().
DOC_META_NAME = "meta.json"

# Kinds that are TABLE-shaped rather than Markdown-shaped. es_read exists to
# page MARKDOWN (reader.py/read.py's whole outline/section/window/query
# machinery assumes prose with optional "## " headings) — a table-kind
# handle must be refused there, with the agent pointed at a query-shaped
# tool instead, rather than silently handed markdown that happens to render
# a table, or an empty/misleading read.
#
# No CONVERTER produces one of these kinds today: doc_text/doc_office's own
# .csv/.xlsx converters still emit kind "csv"/"xlsx" — ordinary Markdown
# pipe tables, fully readable via es_read like any other document. This set
# is empty of real converter output on purpose, ahead of need: a later plan
# converts .csv/.xlsx into a queryable DuckDB database instead (addressed by
# a new es_doc_query tool, not es_read) and will record that conversion's
# `kind` as "table". Defining the set — and reader.py's rejection path that
# checks it — now means that plan lands into a surface that already refuses
# correctly, rather than bolting the refusal on after the fact.
TABLE_KINDS = frozenset({"table"})


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


# Per-converter-module parse-failure handling. Two DIFFERENT mechanisms meet
# here, on purpose:
#
# - doc_pdf raises PdfminerException directly — pdfplumber's own exception
#   type, which ONLY that library raises, never ordinary application code by
#   coincidence (unlike ValueError/KeyError/AttributeError). It is therefore
#   safe to catch broadly around the whole `mod.convert()` call without risk
#   of masking a bug in doc_pdf's own code; see _reraise_pdf_error, which
#   also distinguishes "needs a password" from ordinary corruption by
#   inspecting pdfminer's own wrapped cause.
# - doc_office and doc_text instead raise doc_support.ParseFailed — a
#   sentinel THEY construct themselves, from inside a try/except scoped
#   tightly around their own library's open/parse call only (see each
#   module's own _open_docx/_open_xlsx/_safe_row_iter and
#   _safe_csv_rows/RecursionError handling). Their underlying libraries'
#   parse failures are ordinary types (ValueError, KeyError, AttributeError,
#   ...) that a genuine bug in OUR OWN code could just as easily raise by
#   coincidence — so what makes a failure "a parse failure" here is WHERE it
#   was raised (the narrow try/except inside the converter), never the
#   exception's type. See doc_support.ParseFailed's docstring for the full
#   reasoning. Catching ParseFailed broadly around `mod.convert()` below is
#   safe precisely because a bug elsewhere in doc_office.py/doc_text.py can
#   never raise ParseFailed itself — only their own narrow parse-boundary
#   code does.
#
# doc_ics has neither: it already catches its own parse failures internally
# (see doc_ics._read_calendar's `except Exception: return None`, which
# convert() turns into a friendly in-band "could not be read" markdown
# instead of raising), and that catch is ALREADY scoped to just the parse
# call (`Calendar.from_ical(...)`), so it has none of the masking risk this
# module exists to avoid — nothing to change here.
def _reraise_conversion_error(real: Path, exc: Exception) -> None:
    """Always raises. The one place a converter's parse failure is
    translated into the shared {EncryptedDocument, UnreadableDocument}
    catalogue — kept here (not in each converter module) because docs.py
    already imports every converter module to populate CONVERTERS, so a
    converter importing back from docs.py to raise these classes itself
    would be a circular import."""
    if isinstance(exc, PdfminerException):
        _reraise_pdf_error(real, exc)
    assert isinstance(exc, doc_support.ParseFailed)
    if exc.encrypted:
        raise EncryptedDocument(str(exc)) from exc
    raise UnreadableDocument(str(exc)) from exc


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
      same convention es_read/es_notes_attach/es_notes_list already use
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
        # This check runs against the RAW source file, before pages is ever
        # parsed for either extract() or render() — a page range was never a
        # real escape from it (the whole file must still be read off disk
        # regardless of how much of it is requested), so the remedy names
        # only the thing that actually helps.
        raise DocumentTooLarge(
            f"{real.name} is larger than the {MAX_DOCUMENT_BYTES // (1024*1024)}MB "
            "limit; ask the user for a smaller file")
    did = doc_cache.doc_id(real, ext)
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


def _read_meta_kind(adir: Path) -> Optional[str]:
    """None means "no recorded kind" — a plain absence (an artifact dir
    written before meta.json existed, or one partially purged) or an
    unreadable/corrupt sidecar, treated the same permissively as a missing
    images.json (_read_images_manifest): the cache entry is still a hit,
    just with kind unknown. reader.py treats an unknown kind as NOT a table
    kind (docs.TABLE_KINDS membership fails for None), so a pre-existing
    artifact from before this sidecar existed keeps reading exactly as it
    did before — only a handle that positively recorded a table kind is
    ever refused."""
    meta = adir / DOC_META_NAME
    if not meta.is_file():
        return None
    try:
        k = json.loads(meta.read_text(encoding="utf-8")).get("kind")
    except (OSError, ValueError, AttributeError):
        return None
    return k if isinstance(k, str) else None


def read_cached(adir: Path) -> Optional[dict]:
    """Read a previously cached FULL-document extract (doc.md + its
    images.json/meta.json sidecars) from `adir`, or None if there isn't one
    — never converted, purged, or an undecodable doc.md (treated as a miss,
    not an error; see _read_cached_markdown).

    The one shared accessor for reading a cached conversion: extract()'s own
    cache-hit path below uses it, and so does es_read's `doc:<id>` resolution
    (es/capabilities/reader.py) — neither re-reads doc.md/images.json/
    meta.json on its own, so there is exactly one place that knows a missing
    images.json sidecar means "no images" (_read_images_manifest) and a
    missing meta.json means "kind unknown" (_read_meta_kind) rather than
    "cache broken" — a caller here inherits both for free instead of
    re-deciding them.

    `kind` is what reader.py's `_resolve_doc` checks against
    docs.TABLE_KINDS to refuse a table-shaped handle before ever handing
    back markdown for es_read to page.

    Does NOT touch()/mkdir the directory — that's the caller's call: a
    cache-hit inside extract() touches immediately (a hit there always means
    a real access), but a lookup by doc_id that turns out to be a plain miss
    (no directory at all) has nothing worth touching.
    """
    markdown = _read_cached_markdown(adir)
    if markdown is None:
        return None
    return {"markdown": markdown, "images": _read_images_manifest(adir),
            "kind": _read_meta_kind(adir)}


def _write_full_extract(adir: Path, markdown: str, images: List[Path],
                         kind: Optional[str] = None) -> None:
    """`kind` defaults to None (no meta.json written) rather than being
    required, so a test/fixture built before this sidecar existed (writing
    only doc.md + images.json, e.g. tests/test_reader.py's `_seed_doc`) keeps
    working unchanged — read_cached() already treats a missing meta.json the
    same permissive way it treats a missing images.json (see
    _read_meta_kind)."""
    # meta.json is written FIRST, doc.md LAST: read_cached() gates a cache
    # HIT on doc.md's presence alone (_read_cached_markdown), so a crash
    # between these writes must never leave a doc.md readable without its
    # kind already recorded — that window is harmless today (an unknown
    # kind reads as markdown, which happens to be correct for every kind
    # CONVERTERS produces now) but would silently serve a future "table"
    # artifact to es_read as prose if doc.md ever became readable first.
    if kind is not None:
        (adir / DOC_META_NAME).write_text(
            json.dumps({"kind": kind}), encoding="utf-8")
    (adir / DOC_IMAGES_MANIFEST).write_text(
        json.dumps([str(i) for i in images]), encoding="utf-8")
    (adir / DOC_MD_NAME).write_text(markdown, encoding="utf-8")


def extract(source: str, roots, cache_root: Path,
            image_pages: Optional[str] = None) -> dict:
    """image_pages is ADDITIVE to the conversion, never a second document:
    doc_id is a pure content-and-format hash (see doc_cache.doc_id) and does
    not fold in whether/which pages were rendered, so calling extract() again
    with a DIFFERENT image_pages against the same source is still the same
    doc_id, still a conversion cache hit — it only does the extra rendering
    work, into the SAME artifact dir the plain conversion already lives in
    (doc_cache.page_image_path's `image_no=None` bare `pNNN.png` form exists
    for exactly this: a page can have both its own extracted/drawing images
    AND a separate whole-page render, without collision). This is why
    image_pages is a parameter here rather than a distinct render() entry
    point with its own doc_id/cache path — one document, optionally with a
    few of its pages ALSO available as whole-page PNGs.
    """
    real, did, adir = _prepare(source, roots, cache_root)
    ext = real.suffix.lower()
    mod = CONVERTERS[ext]
    total = _page_count(mod, real)
    # Trivially the extension without its dot ("pdf" for ".pdf") — correct
    # today and requires no per-format table of its own. Computed once, up
    # front, so both the meta.json sidecar (written below) and the receipt's
    # own `kind` field (returned at the bottom) always agree.
    kind = ext.lstrip(".")

    # Full-document extract only: doc_id is a content hash, so a previous
    # conversion of this exact content is still correct — check the cache
    # before paying for another convert(). (A page-SUBSET extract used to be
    # a second, uncached code path here — removed along with the `pages`
    # argument: `section="page-37"` through es_read already expresses that
    # intent, and the subset path was never cached anyway, so it produced a
    # dead `doc:<id>` handle and an error telling the agent to retry the very
    # thing that had just failed.)
    cached = read_cached(adir)
    if cached is not None:
        doc_cache.touch(adir)  # TTL means "24h since last USE", not "24h
                                # since conversion" — a cache hit is a use.
        markdown = cached["markdown"]
    else:
        try:
            markdown, image_paths = mod.convert(real, adir, pages=None)
        except (doc_support.ParseFailed, PdfminerException) as e:
            _reraise_conversion_error(real, e)
        _write_full_extract(adir, markdown, image_paths, kind=kind)
        doc_cache.touch(adir)

    # Rendering is entirely separate from (and always runs after) the
    # conversion/cache step above — a cache HIT still renders whatever pages
    # were newly asked for, since image_pages is not part of doc_id and so
    # was never part of what made this a cache hit in the first place.
    page_images: List[str] = []
    if image_pages is not None:
        # Rasterizing pages only means something for a format that HAS pages
        # to rasterize — a converter without `render` (every flat format:
        # txt/md/csv/json, all via doc_text) can't support this at all,
        # independent of whatever image_pages was passed, so the capability
        # check comes first and names the actual reason rather than failing
        # confusingly deeper down (e.g. inside parse_pages against a
        # page_count that doesn't exist). hasattr mirrors the same
        # "presence of the optional attribute IS the capability" convention
        # _page_count already uses for page_count.
        if not hasattr(mod, "render"):
            raise UnsupportedDocument(
                f"{ext} cannot be rendered to images — image_pages only "
                "works for a PDF; call es_doc_extract without image_pages "
                "for this format")
        wanted = parse_pages(image_pages, total)
        if len(wanted) > MAX_IMAGE_PAGES:
            raise InvalidPageRange(
                f"cannot render {len(wanted)} pages in one call — the limit "
                f"is {MAX_IMAGE_PAGES}; narrow the range (call again for "
                "the rest)")
        rendered = mod.render(real, adir, wanted)
        doc_cache.touch(adir)
        page_images = [str(p) for p in rendered]

    # A RECEIPT, not the document: doc.md (written above, in full, before any
    # of this) is what es_read pages — this return value only has to let the
    # agent identify what it's holding and decide whether it needs to call
    # es_read at all.
    complete = len(markdown) <= PREVIEW_CHARS
    # A raw markdown[:PREVIEW_CHARS] slice can land inside a single-line
    # "![page N](path)" image link (doc_pdf emits one per rendered/scanned
    # page) — with production-length cache paths this happens for every
    # scanned PDF of 7+ pages, handing the agent a truncated, unusable path
    # on its very first interaction with the document. Only cut with
    # doc_support.rfind_safe_cut (never slicing mid-line) when a cut is
    # actually needed — when `complete` is already true, `preview` must
    # stay the untouched, full markdown, not a newline-trimmed copy of it.
    preview = markdown if complete else markdown[:doc_support.rfind_safe_cut(markdown, PREVIEW_CHARS)]
    # Always names BOTH the tool and the handle — even when complete=true —
    # so the agent copies `next` verbatim rather than constructing a
    # "doc:<id>" string itself (a transcription slip there is exactly the
    # DocHandleExpired failure mode reader.py's hex check guards against).
    # When image_pages was given, the genuinely next thing to do is look at
    # the rendered page(s) — that IS the reason this call was made — not
    # page through the (already-known-to-be-unhelpful) text via es_read.
    if page_images:
        rendered_repr = ", ".join(f'"{p}"' for p in page_images)
        next_step = f'call vision_analyze on {rendered_repr} to see the rendered page(s)'
    else:
        next_step = (
            f'preview is the whole document — nothing else to call for this '
            f'document (es_read(target="doc:{did}") would just return it again)'
            if complete else
            f'call es_read(target="doc:{did}") to read the rest, paged by heading'
        )
    return {"doc_id": did, "kind": kind, "page_count": total,
            "preview": preview, "complete": complete,
            "page_images": page_images, "next": next_step}
