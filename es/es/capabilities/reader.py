"""Resolve an es_read `target` string to Markdown plus source metadata.

This is the one place "what to read" turns into "here is the markdown", so
that capabilities/read.py's pure primitives (outline/section/window/search)
have exactly one source of text to operate over, regardless of whether the
underlying thing is a vault note or a converted document. No parsing of the
Markdown itself happens here — that's read.py's job; this module is purely
about fetching the right string.

Two forms of `target`:

- "doc:<id>" — a previously cached es_doc_extract result, addressed by the
  doc_id that call returned. Looked up directly in the cache (never
  reconverts, never re-runs a converter) and TOUCHED on every successful
  read: the artifact TTL is 24h since last ACCESS (doc_cache.touch), not
  since conversion, and reading a document through es_read is exactly as
  much a "use" of it as reading it through es_doc_extract was — so an
  in-progress conversation that keeps paging through a document via es_read
  must keep it alive the same way repeated es_doc_extract calls already do.

- anything else — a vault note, addressed by path or by topic name exactly
  as vault_client.read_note already accepts. This module adds NO new
  note-addressing semantics; it is a thin pass-through so es_read needs no
  path-or-topic logic of its own (same resolution, same traversal
  confinement, both already enforced inside VaultClient._resolve/_within_root).

Frontmatter is returned for a note (not just its body): the retired
es_notes_read tool returned {path, frontmatter, body}, and the agent may
rely on frontmatter (topics, tags, created) to decide what to do next —
dropping it here would be a regression now that es_read is the only read
path for notes.
"""
import re
from pathlib import Path

from es import doc_cache
from es.capabilities import docs
from es.vault_client import VaultClient

DOC_PREFIX = "doc:"

# doc_id is always a hex sha256 prefix (doc_cache.doc_id/DOC_ID_LEN) — this
# is the ONLY thing that stands between a `doc:<id>` target and a path join
# under the cache root, so it is intentionally strict: any non-hex
# character (a '/', a '.', a null byte, ...) fails the match, and a target
# like "doc:../../etc/passwd" is rejected right here, before anything is
# ever joined onto a Path. It is reported as DocHandleExpired — the same
# error as a real-but-purged id — rather than a distinct "invalid handle"
# error: both cases mean "this is not a document es_read can serve you right
# now", and the remedy (call es_doc_extract again) is identical either way.
_DOC_ID_RE = re.compile(r"^[0-9a-f]+$")


class DocHandleExpired(Exception):
    es_code = "doc_handle_expired"


class TableKindNotReadable(Exception):
    """Raised when a `doc:<id>` handle's recorded kind is table-shaped
    (docs.TABLE_KINDS) — es_read pages MARKDOWN (read.py's outline/section/
    window/search machinery all assume prose with optional "## " headings),
    so a table-kind handle must error here rather than come back as a null-
    filled or empty envelope. The message always names es_doc_query (the
    tool a table handle is meant to be read through instead) as the remedy.

    Not a nicety: a table artifact has no doc.md at all (read_cached() gates
    it on tables.json and the database instead), so without this the agent
    would be told a perfectly good spreadsheet was an expired handle.
    """
    es_code = "doc_table_kind"


class NotATableDocument(Exception):
    """The mirror of TableKindNotReadable, raised by resolve_table when
    es_doc_query is pointed at a MARKDOWN handle (or at something that isn't
    a handle at all). Both errors exist for the same reason: the agent has
    two read tools and one document, and picking the wrong one must always
    name the right one rather than return an empty result."""
    es_code = "doc_not_table"


def _load_doc(doc_id: str, cache_root: Path):
    """Validate, locate, load and TOUCH one cached artifact — everything both
    read paths need before they diverge on what kind it turned out to be.
    Returns (handle, adir, cached)."""
    if not _DOC_ID_RE.match(doc_id):
        raise DocHandleExpired(
            f"{DOC_PREFIX}{doc_id!r} is not a valid document handle — call "
            "es_doc_extract on the source file to get a current one")
    adir = Path(cache_root) / doc_cache.ES_NAMESPACE / doc_id
    cached = docs.read_cached(adir)
    if cached is None:
        raise DocHandleExpired(
            f"no cached document for {DOC_PREFIX}{doc_id} — it may have "
            "aged out of the cache (documents are kept for 24 hours since "
            "last use); call es_doc_extract again on the source file")
    doc_cache.touch(adir)  # a read through es_read is a use, same as a
                            # cache-hit inside es_doc_extract itself — true
                            # even for a handle rejected below as the wrong
                            # kind, since the agent still just looked it up.
    return f"{DOC_PREFIX}{doc_id}", adir, cached


def resolve_table(target: str, cache_root: Path) -> dict:
    """The es_doc_query counterpart to resolve(): the same handle lookup, with
    the kind check running the other way round.

    Returns {"handle", "doc_id", "adir", "tables"}. Raises DocHandleExpired
    for an unknown/malformed/aged-out handle, or NotATableDocument when the
    handle names a MARKDOWN document — the exact mirror of es_read's
    TableKindNotReadable, so whichever of the two tools the agent reaches for
    first, being wrong points it straight at the other one instead of
    returning something empty or confusing.
    """
    if not target.startswith(DOC_PREFIX):
        raise NotATableDocument(
            f"{target!r} is not a document handle — es_doc_query only runs "
            f'against a "{DOC_PREFIX}<id>" returned by es_doc_extract, never '
            "a file path or a vault note (call es_read for a note)")
    handle, adir, cached = _load_doc(target[len(DOC_PREFIX):], cache_root)
    kind = cached["kind"]
    if kind not in docs.TABLE_KINDS:
        raise NotATableDocument(
            f"{handle} is a {kind or 'markdown'} document, not tabular data "
            f'— there is nothing to query; call es_read(target="{handle}") '
            "to read it instead")
    return {"handle": handle, "doc_id": target[len(DOC_PREFIX):],
            "adir": adir, "tables": cached["tables"]}


def _resolve_doc(doc_id: str, cache_root: Path) -> dict:
    handle, adir, cached = _load_doc(doc_id, cache_root)
    kind = cached["kind"]
    if kind in docs.TABLE_KINDS:
        raise TableKindNotReadable(
            f"{handle} is a {kind} document — es_read only reads Markdown, "
            f"never tabular data; call es_doc_query(target=\"{handle}\") "
            "to query it instead")
    return {"kind": "doc", "source": handle,
            "doc_id": doc_id, "markdown": cached["markdown"]}


def _resolve_note(target: str, vault: VaultClient) -> dict:
    note = vault.read_note(target)
    return {"kind": "note", "source": note["path"], "path": note["path"],
            "frontmatter": note["frontmatter"], "markdown": note["body"]}


def resolve(target: str, *, vault: VaultClient, cache_root: Path) -> dict:
    """Resolve `target` to {"kind", "source", "markdown", ...}.

    `kind` is "note" or "doc"; `source` is a canonical identifier for what
    was read (the note's vault-relative path, or "doc:<id>") so a caller can
    always name what it just read regardless of which branch served it. A
    note's dict additionally carries `path` (== source) and `frontmatter`.

    Raises vault_client.NoteNotFound for an unknown note path/topic,
    DocHandleExpired for an unknown, malformed, or aged-out `doc:<id>`, or
    TableKindNotReadable for a `doc:<id>` whose recorded kind is
    table-shaped (docs.TABLE_KINDS) — es_read pages Markdown only.
    """
    if target.startswith(DOC_PREFIX):
        return _resolve_doc(target[len(DOC_PREFIX):], cache_root)
    return _resolve_note(target, vault)
