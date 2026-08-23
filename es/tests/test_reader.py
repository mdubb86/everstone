"""es.capabilities.reader.resolve — turning an es_read `target` into
Markdown, for both vault notes and cached document conversions.

Exercises reader.resolve() directly against a real VaultClient/cache_root
(no MCP layer, no config.py) — the signature is explicit/injectable on
purpose so these tests don't need a container.
"""
import os
import time

import pytest

from es import doc_cache
from es.capabilities import docs, reader
from es.vault_client import NoteNotFound, VaultClient


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    return VaultClient(root, "TestVault", categories=["Topics"])


@pytest.fixture
def cache_root(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return d


# --- vault notes -------------------------------------------------------

def test_resolve_note_by_path_returns_markdown_body(vault):
    vault.write_topic("Manual", body="# Manual\n\nRead me.")
    out = reader.resolve("Topics/Manual.md", vault=vault, cache_root=None)
    assert out["kind"] == "note"
    assert "Read me." in out["markdown"]
    assert out["path"] == "Topics/Manual.md"
    assert out["source"] == "Topics/Manual.md"


def test_resolve_note_by_topic_name_matches_by_path_resolution(vault):
    """Preserves es_notes_read's existing target semantics exactly: a bare
    topic name resolves the same note as its vault-relative path."""
    vault.write_topic("Manual", body="# Manual\n\nRead me.")
    by_path = reader.resolve("Topics/Manual.md", vault=vault, cache_root=None)
    by_topic = reader.resolve("Manual", vault=vault, cache_root=None)
    assert by_topic["markdown"] == by_path["markdown"]
    assert by_topic["path"] == by_path["path"]
    assert by_topic["source"] == by_path["source"]


def test_resolve_note_returns_frontmatter(vault):
    """es_notes_read returns {path, frontmatter, body} and the agent relies
    on frontmatter (topics, tags, created) — dropping it would be a
    regression once es_notes_read is retired in favor of es_read."""
    vault.write_journal("Practice moved", "Body text.", tags=["soccer"],
                        topics=["Thunder U10"])
    entries = vault.list_journal()
    assert len(entries) == 1
    out = reader.resolve(entries[0]["path"], vault=vault, cache_root=None)
    assert out["frontmatter"].get("tags") == ["soccer"]
    assert out["frontmatter"].get("topics") == ["[[Thunder U10]]"]
    assert "created" in out["frontmatter"]


def test_resolve_missing_note_path_raises_unchanged_not_found(vault):
    with pytest.raises(NoteNotFound):
        reader.resolve("Topics/Nope.md", vault=vault, cache_root=None)


# --- cached documents ("doc:<id>") -------------------------------------

def test_resolve_doc_handle_returns_cached_markdown(text_pdf, cache_root):
    extracted = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=cache_root)
    target = f"doc:{extracted['doc_id']}"

    out = reader.resolve(target, vault=None, cache_root=cache_root)
    assert out["kind"] == "doc"
    assert out["doc_id"] == extracted["doc_id"]
    assert out["source"] == target
    assert out["markdown"] == extracted["markdown"]
    assert "Fall Season Schedule" in out["markdown"]


def test_resolve_unknown_doc_handle_raises_expired_naming_the_remedy(cache_root):
    with pytest.raises(reader.DocHandleExpired) as e:
        reader.resolve("doc:deadbeef0000", vault=None, cache_root=cache_root)
    assert e.value.es_code == "doc_handle_expired"
    assert "es_doc_extract" in str(e.value)


def test_resolve_doc_handle_touches_the_artifact_dir(text_pdf, cache_root):
    """The cache TTL is 24h since last ACCESS (doc_cache.touch) — reading a
    doc: handle through es_read must count as an access, the same way a
    cache-hit inside es_doc_extract already does. Regression pattern mirrors
    test_touch_makes_a_stale_directory_fresh in test_doc_cache.py."""
    extracted = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=cache_root)
    doc_id = extracted["doc_id"]
    adir = doc_cache.artifact_dir(cache_root, doc_id)

    stale = time.time() - (25 * 3600)
    os.utime(adir, (stale, stale))

    reader.resolve(f"doc:{doc_id}", vault=None, cache_root=cache_root)

    assert adir.stat().st_mtime > stale
    assert doc_cache.purge(cache_root) == 0  # would have been evicted had
                                              # the read not touched it


def test_resolve_doc_handle_survives_a_missing_images_sidecar(text_pdf, cache_root):
    """A partially-purged artifact dir (images.json gone, doc.md intact)
    must still resolve — reader.py must inherit docs.py's existing
    missing-sidecar tolerance (_read_images_manifest) rather than working
    around it a second time."""
    extracted = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=cache_root)
    adir = doc_cache.artifact_dir(cache_root, extracted["doc_id"])
    (adir / docs.DOC_IMAGES_MANIFEST).unlink()

    out = reader.resolve(f"doc:{extracted['doc_id']}", vault=None, cache_root=cache_root)
    assert out["markdown"] == extracted["markdown"]


# --- target cannot escape the vault or the cache ------------------------

def test_doc_handle_traversal_is_rejected_as_expired_not_a_path_error(cache_root):
    """A doc: id is never joined onto a path unless it's pure hex — a
    traversal attempt fails the hex check and comes back as the same
    DocHandleExpired an unknown id would, never touching the filesystem
    outside cache_root."""
    with pytest.raises(reader.DocHandleExpired):
        reader.resolve("doc:../../../etc/passwd", vault=None, cache_root=cache_root)


def test_note_path_traversal_is_rejected_unchanged(vault):
    """Vault confinement (VaultClient._within_root) is untouched by this
    module — a traversal attempt still surfaces as NoteNotFound, exactly as
    es_notes_read already behaves."""
    with pytest.raises(NoteNotFound):
        reader.resolve("../../../../etc/passwd", vault=vault, cache_root=None)
