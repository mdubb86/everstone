"""es.capabilities.reader.resolve — turning an es_read `target` into
Markdown, for both vault notes and cached document conversions.

Exercises reader.resolve() directly against a real VaultClient/cache_root
(no MCP layer, no config.py) — the signature is explicit/injectable on
purpose so these tests don't need a container.
"""
import os
import time

import pytest

from es import doc_cache, mcp_server
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
    """Preserves the retired es_notes_read's target semantics exactly: a bare
    topic name resolves the same note as its vault-relative path."""
    vault.write_topic("Manual", body="# Manual\n\nRead me.")
    by_path = reader.resolve("Topics/Manual.md", vault=vault, cache_root=None)
    by_topic = reader.resolve("Manual", vault=vault, cache_root=None)
    assert by_topic["markdown"] == by_path["markdown"]
    assert by_topic["path"] == by_path["path"]
    assert by_topic["source"] == by_path["source"]


def test_resolve_note_returns_frontmatter(vault):
    """The retired es_notes_read returned {path, frontmatter, body} and the
    agent relies on frontmatter (topics, tags, created) — dropping it would
    be a regression now that es_read is the only read path for notes."""
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
    the retired es_notes_read already behaved."""
    with pytest.raises(NoteNotFound):
        reader.resolve("../../../../etc/passwd", vault=vault, cache_root=None)


# --- the es_read MCP tool -----------------------------------------------
#
# These exercise es_read end to end (through mcp_server, through
# reader.resolve, through the read.py primitives) rather than any one layer
# in isolation — the point of the tool is how those layers compose, and the
# stable-envelope / whole-vs-outline behaviour only exists at this level.

ENVELOPE_KEYS = {"kind", "source", "path", "frontmatter", "content",
                 "outline", "more", "next_offset"}


@pytest.fixture
def wired_vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    v = VaultClient(root, "TestVault", categories=["Topics"])
    monkeypatch.setattr(mcp_server, "_notes_client", lambda: v)
    return v


@pytest.fixture
def wired_cache(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    d.mkdir()
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: d)
    return d


def _big_markdown(n: int = 150) -> str:
    """A synthetic document comfortably over es_read's whole-vs-outline
    threshold, shaped like a converted calendar: a short preamble line, then
    many small `##` sections (deliberately small individually — the point of
    the 100+-event calendar case is that no SINGLE section is large)."""
    parts = [f"{n} events (0 recurring)"]
    for i in range(n):
        parts.append(f"\n## Event {i}\n\nDetails for event {i}.\n")
    return "\n".join(parts)


def test_es_read_small_note_returns_whole_content(wired_vault):
    wired_vault.write_topic("Manual", body="# Manual\n\nRead me in full.")
    out = mcp_server.es_read("Manual")
    assert out["ok"] is True
    data = out["data"]
    assert data["content"].strip() == "# Manual\n\nRead me in full."
    assert data["more"] is False
    assert data["next_offset"] is None
    assert set(data.keys()) == ENVELOPE_KEYS


def test_es_read_large_document_returns_outline_not_everything(wired_vault):
    """The core behaviour: a large multi-section document (standing in for
    the 117-event-calendar case) must not dump every section — it comes
    back as an outline to choose from, plus a short preamble preview, with
    more=true telling the agent there's content it hasn't seen yet."""
    big = _big_markdown(150)
    wired_vault.write_topic("BigCal", body=big)

    out = mcp_server.es_read("BigCal")
    assert out["ok"] is True
    data = out["data"]
    assert len(data["outline"]) == 150
    assert data["outline"][0]["title"] == "Event 0"
    assert data["more"] is True
    assert data["content"] == "150 events (0 recurring)"
    # the full 150 event bodies must NOT be dumped into `content`
    assert "Details for event 149" not in (data["content"] or "")
    assert set(data.keys()) == ENVELOPE_KEYS


def test_es_read_section_returns_only_that_section(wired_vault):
    big = _big_markdown(150)
    wired_vault.write_topic("BigCal", body=big)
    outline = mcp_server.es_read("BigCal")["data"]["outline"]
    target_id = next(s["id"] for s in outline if s["title"] == "Event 7")

    out = mcp_server.es_read("BigCal", section=target_id)
    assert out["ok"] is True
    data = out["data"]
    assert "Details for event 7." in data["content"]
    assert "Event 8" not in data["content"]
    assert data["more"] is True  # other sections remain
    assert set(data.keys()) == ENVELOPE_KEYS


def test_es_read_query_returns_matching_ids(wired_vault):
    wired_vault.write_topic(
        "Team", body="## Roster\n\nNo mention here.\n\n## Nickname\n\nThe Fury.\n")
    out = mcp_server.es_read("Team", query="fury")
    assert out["ok"] is True
    data = out["data"]
    assert [s["title"] for s in data["outline"]] == ["Nickname"]
    assert data["content"] is None  # query hands back ids to follow up with, not text
    assert set(data.keys()) == ENVELOPE_KEYS


def test_es_read_query_with_no_hits_explains_what_to_try(wired_vault):
    wired_vault.write_topic("Team", body="## Roster\n\nNothing relevant.\n")
    out = mcp_server.es_read("Team", query="zebra")
    assert out["ok"] is True
    data = out["data"]
    assert data["outline"] == []
    assert data["content"]  # not empty — tells the agent what to try next
    assert "zebra" in data["content"]
    assert set(data.keys()) == ENVELOPE_KEYS


def test_es_read_offset_on_headingless_document_reports_next_offset(wired_vault):
    # 500 rows: with the default 200-line window, offset=100 leaves rows still
    # unread past this page (100:300), so next_offset must be populated.
    csv_body = "\n".join(f"row{i},value{i}" for i in range(500))
    wired_vault.write_topic("Data", body=csv_body)

    out = mcp_server.es_read("Data", offset=100)
    assert out["ok"] is True
    data = out["data"]
    assert data["content"].splitlines()[0] == "row100,value100"
    assert data["next_offset"] == 300
    assert data["more"] is True
    assert data["outline"] is None
    assert set(data.keys()) == ENVELOPE_KEYS


def test_es_read_unknown_section_is_no_such_section(wired_vault):
    wired_vault.write_topic("Manual", body="## Real Section\n\nBody.\n")
    out = mcp_server.es_read("Manual", section="nope")
    assert out["ok"] is False
    assert out["error"]["code"] == "no_such_section"
    assert "real-section" in out["error"]["message"]


def test_es_read_expired_doc_handle(wired_cache):
    out = mcp_server.es_read("doc:deadbeef0000")
    assert out["ok"] is False
    assert out["error"]["code"] == "doc_handle_expired"
    assert "es_doc_extract" in out["error"]["message"]


def test_es_read_note_by_path_and_topic_name_match_with_frontmatter(wired_vault):
    """A topic doc is addressable both by its vault-relative path and by its
    bare topic name (VaultClient._find_topic) — both must resolve to the
    same content, with frontmatter intact either way."""
    wired_vault.write_topic(
        "Manual", body="---\ntags: [important]\n---\n\n# Manual\n\nContent.\n")

    by_path = mcp_server.es_read("Topics/Manual.md")["data"]
    by_topic = mcp_server.es_read("Manual")["data"]

    assert by_path["content"] == by_topic["content"]
    assert by_path["path"] == by_topic["path"] == "Topics/Manual.md"
    assert by_path["frontmatter"]["tags"] == ["important"]
    assert by_topic["frontmatter"]["tags"] == ["important"]


def test_es_read_doc_handle_reads_a_converted_document(text_pdf, wired_cache):
    extracted = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=wired_cache)
    out = mcp_server.es_read(f"doc:{extracted['doc_id']}")
    assert out["ok"] is True
    data = out["data"]
    assert data["kind"] == "doc"
    assert data["path"] is None
    assert data["frontmatter"] is None
    assert "Fall Season Schedule" in data["content"]
    assert set(data.keys()) == ENVELOPE_KEYS


def test_es_read_envelope_key_set_is_identical_across_all_modes(wired_vault):
    """The agent must not have to reason about which keys exist depending on
    which mode it used — every mode returns exactly the same key set."""
    big = _big_markdown(150)
    wired_vault.write_topic("BigCal", body=big)
    wired_vault.write_topic("Small", body="# Small\n\nWhole thing.")
    section_id = mcp_server.es_read("BigCal")["data"]["outline"][0]["id"]

    modes = [
        mcp_server.es_read("Small"),
        mcp_server.es_read("BigCal"),
        mcp_server.es_read("BigCal", section=section_id),
        mcp_server.es_read("BigCal", query="Event 3"),
        mcp_server.es_read("BigCal", query="no-such-text-anywhere"),
    ]
    for out in modes:
        assert out["ok"] is True
        assert set(out["data"].keys()) == ENVELOPE_KEYS
