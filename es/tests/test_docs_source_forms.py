"""Path-form support for es_doc_extract/es_doc_render's `source` argument.

Three... no, two accepted forms today (a third, "$cache/...", was considered
and dropped — see docs.py's _expand_source docstring: every Telegram upload
already arrives as an absolute path, so there is no producer anywhere that
hands the agent a cache-relative string to expand):

1. An absolute path — unchanged, still confined to whatever `roots` are
   passed in (mirrors today's only working form).
2. A vault-relative path, either as a bare relative string (matching
   es_notes_read/es_notes_attach/es_notes_list's existing convention — they
   hand back exactly this form) or the explicit "$vault/..." synonym.

These tests exercise es.capabilities.docs._expand_source/_prepare (via
extract()/render()) directly against real ES_VAULT_PATH-backed roots, plus
the MCP tool layer end to end.
"""
from pathlib import Path

import pytest

from es import config
from es.capabilities import docs


@pytest.fixture
def vault_and_cache(tmp_path, monkeypatch):
    """A real vault dir (wired through ES_VAULT_PATH, exactly like config.py
    expects in production) plus a separate cache dir standing in for the
    Telegram media cache. Returns (vault_dir, cache_dir, roots)."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("ES_VAULT_PATH", str(vault_dir))
    roots = [cache_dir, vault_dir]
    return vault_dir, cache_dir, roots


def _drop_pdf(dest: Path, text_pdf) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(text_pdf.read_bytes())
    return dest


# --- vault-relative and $vault/ forms resolve correctly ---------------------

def test_bare_relative_path_resolves_against_the_vault(vault_and_cache, text_pdf):
    vault_dir, cache_dir, roots = vault_and_cache
    dest = _drop_pdf(vault_dir / "Topics" / "Manual.pdf", text_pdf)

    out = docs.extract("Topics/Manual.pdf", roots=roots, cache_root=vault_dir)
    assert "Fall Season Schedule" in out["markdown"]
    assert config.vault_root() == vault_dir
    assert dest.is_file()


def test_dollar_vault_prefix_resolves_against_the_vault(vault_and_cache, text_pdf):
    vault_dir, cache_dir, roots = vault_and_cache
    _drop_pdf(vault_dir / "Topics" / "Manual.pdf", text_pdf)

    out = docs.extract("$vault/Topics/Manual.pdf", roots=roots, cache_root=vault_dir)
    assert "Fall Season Schedule" in out["markdown"]


def test_dollar_vault_and_bare_relative_are_interchangeable(vault_and_cache, text_pdf):
    """Same file, addressed the two different vault-relative ways, must
    produce identical results (same doc_id, same markdown)."""
    vault_dir, cache_dir, roots = vault_and_cache
    _drop_pdf(vault_dir / "Topics" / "Manual.pdf", text_pdf)

    via_bare = docs.extract("Topics/Manual.pdf", roots=roots, cache_root=vault_dir)
    via_prefix = docs.extract("$vault/Topics/Manual.pdf", roots=roots, cache_root=vault_dir)
    assert via_bare["doc_id"] == via_prefix["doc_id"]
    assert via_bare["markdown"] == via_prefix["markdown"]


def test_absolute_path_inside_vault_is_interchangeable_with_vault_relative(
        vault_and_cache, text_pdf):
    """The coordinator's question, answered empirically: an absolute path
    INSIDE the vault must behave identically to its vault-relative
    equivalent — same doc_id, same markdown, same page_count. All three
    forms name the exact same file."""
    vault_dir, cache_dir, roots = vault_and_cache
    dest = _drop_pdf(vault_dir / "Topics" / "Manual.pdf", text_pdf)

    via_absolute = docs.extract(str(dest), roots=roots, cache_root=vault_dir)
    via_bare = docs.extract("Topics/Manual.pdf", roots=roots, cache_root=vault_dir)
    via_prefix = docs.extract("$vault/Topics/Manual.pdf", roots=roots, cache_root=vault_dir)

    assert via_absolute["doc_id"] == via_bare["doc_id"] == via_prefix["doc_id"]
    assert via_absolute["markdown"] == via_bare["markdown"] == via_prefix["markdown"]
    assert via_absolute["page_count"] == via_bare["page_count"] == via_prefix["page_count"]


def test_absolute_path_outside_vault_but_inside_cache_still_works(
        vault_and_cache, text_pdf):
    """Absolute-path behavior for the (non-vault) cache root is unchanged."""
    vault_dir, cache_dir, roots = vault_and_cache
    dest = _drop_pdf(cache_dir / "upload.pdf", text_pdf)

    out = docs.extract(str(dest), roots=roots, cache_root=vault_dir)
    assert "Fall Season Schedule" in out["markdown"]


def test_absolute_path_is_never_reinterpreted_as_vault_relative(vault_and_cache, text_pdf):
    """A file that happens to sit in the cache dir, addressed absolutely,
    must not be silently re-rooted onto the vault (there is no vault-relative
    spelling of a cache-only file, and none should be invented)."""
    vault_dir, cache_dir, roots = vault_and_cache
    dest = _drop_pdf(cache_dir / "upload.pdf", text_pdf)

    # A same-named file does NOT exist under the vault — proves the absolute
    # cache path resolved to the real cache file, not some reinterpreted
    # vault-relative path.
    assert not (vault_dir / "upload.pdf").exists()
    out = docs.extract(str(dest), roots=roots, cache_root=vault_dir)
    assert out["doc_id"] == docs.doc_cache.doc_id(dest)


# --- traversal is rejected, not silently escaped ----------------------------

def test_dollar_vault_traversal_is_rejected(vault_and_cache):
    vault_dir, cache_dir, roots = vault_and_cache
    with pytest.raises(docs.paths.SourceForbidden):
        docs.extract("$vault/../../../../../../etc/passwd", roots=roots,
                     cache_root=vault_dir)


def test_bare_relative_traversal_escaping_the_vault_is_rejected(vault_and_cache):
    vault_dir, cache_dir, roots = vault_and_cache
    with pytest.raises(docs.paths.SourceForbidden):
        docs.extract("../../../../../../etc/passwd", roots=roots, cache_root=vault_dir)


def test_dollar_vault_traversal_rejected_even_with_only_two_dotdots(vault_and_cache, text_pdf):
    """A shallower, more directly-plausible escape attempt (climbing out of
    the vault into its sibling cache dir and beyond) is rejected the same
    way — not just implausibly deep ones."""
    vault_dir, cache_dir, roots = vault_and_cache
    # vault_dir and cache_dir are siblings under tmp_path; ../secret.pdf from
    # the vault root lands in tmp_path, outside both allowed roots.
    secret = vault_dir.parent / "secret.pdf"
    secret.write_bytes(b"%PDF-1.4 not a real reader target")
    with pytest.raises(docs.paths.SourceForbidden):
        docs.extract("$vault/../secret.pdf", roots=roots, cache_root=vault_dir)


# --- a literal "$vault"-named file is not surprising -------------------------

def test_literal_dollar_vault_named_file_is_read_as_a_plain_relative_path(
        vault_and_cache, text_pdf):
    """A file literally named "$vault" (no trailing slash, so it never
    matches the prefix) must be reachable as an ordinary vault-relative path
    — not specially intercepted or expanded to the vault root itself."""
    vault_dir, cache_dir, roots = vault_and_cache
    dest = _drop_pdf(vault_dir / "$vault.pdf", text_pdf)  # literal '$vault' basename

    out = docs.extract("$vault.pdf", roots=roots, cache_root=vault_dir)
    assert "Fall Season Schedule" in out["markdown"]
    assert dest.is_file()


# --- forbidden-vs-not-found oracle stays closed for the new forms -----------

def test_oracle_closed_for_vault_relative_forbidden_paths(vault_and_cache):
    """A vault-relative path that resolves OUTSIDE every allowed root (e.g.
    the confinement roots don't actually include the vault) must fail
    identically whether or not a same-named file exists elsewhere on disk —
    same property test_paths.py already pins for absolute paths, now checked
    for the expanded form too."""
    vault_dir, cache_dir, roots = vault_and_cache
    # Roots that do NOT include the vault at all -> any vault-relative source
    # is forbidden regardless of whether the file exists.
    narrow_roots = [cache_dir]

    (vault_dir / "exists.pdf").write_bytes(b"%PDF-1.4 x")
    with pytest.raises(docs.paths.SourceForbidden) as e_exists:
        docs.extract("exists.pdf", roots=narrow_roots, cache_root=vault_dir)
    with pytest.raises(docs.paths.SourceForbidden) as e_missing:
        docs.extract("missing.pdf", roots=narrow_roots, cache_root=vault_dir)

    msg_exists = str(e_exists.value).replace(str(vault_dir / "exists.pdf"), "<PATH>")
    msg_missing = str(e_missing.value).replace(str(vault_dir / "missing.pdf"), "<PATH>")
    assert msg_exists == msg_missing
    assert "exist" not in msg_missing.lower()


def test_forbidden_message_names_only_the_supported_forms(vault_and_cache):
    vault_dir, cache_dir, roots = vault_and_cache
    with pytest.raises(docs.paths.SourceForbidden) as e:
        docs.extract("../../../../../../etc/passwd", roots=[cache_dir], cache_root=vault_dir)
    msg = str(e.value)
    assert "$vault/" in msg
    assert "vault-relative" in msg or "Topics/Manual.pdf" in msg
    assert "$cache" not in msg


# --- MCP tool layer, end to end ---------------------------------------------

def test_mcp_es_doc_extract_accepts_dollar_vault_prefix(vault_and_cache, text_pdf, monkeypatch):
    from es import mcp_server

    vault_dir, cache_dir, roots = vault_and_cache
    _drop_pdf(vault_dir / "Topics" / "Manual.pdf", text_pdf)
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [str(cache_dir), str(vault_dir)])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: vault_dir)

    out = mcp_server.es_doc_extract("$vault/Topics/Manual.pdf")
    assert out["ok"] is True
    assert "Fall Season Schedule" in out["data"]["markdown"]


def test_mcp_es_doc_extract_accepts_bare_vault_relative_path(vault_and_cache, text_pdf, monkeypatch):
    from es import mcp_server

    vault_dir, cache_dir, roots = vault_and_cache
    _drop_pdf(vault_dir / "Topics" / "Manual.pdf", text_pdf)
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [str(cache_dir), str(vault_dir)])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: vault_dir)

    out = mcp_server.es_doc_extract("Topics/Manual.pdf")
    assert out["ok"] is True
    assert "Fall Season Schedule" in out["data"]["markdown"]


def test_mcp_es_doc_render_accepts_vault_relative_forms(vault_and_cache, text_pdf, monkeypatch):
    from es import mcp_server

    vault_dir, cache_dir, roots = vault_and_cache
    _drop_pdf(vault_dir / "Topics" / "Manual.pdf", text_pdf)
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [str(cache_dir), str(vault_dir)])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: vault_dir)

    out_bare = mcp_server.es_doc_render("Topics/Manual.pdf", pages="1")
    out_prefix = mcp_server.es_doc_render("$vault/Topics/Manual.pdf", pages="1")
    assert out_bare["ok"] is True
    assert out_prefix["ok"] is True
    assert len(out_bare["data"]["images"]) == 1
    assert len(out_prefix["data"]["images"]) == 1


def test_mcp_es_doc_extract_still_rejects_traversal_via_dollar_vault(
        vault_and_cache, monkeypatch):
    from es import mcp_server

    vault_dir, cache_dir, roots = vault_and_cache
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [str(cache_dir), str(vault_dir)])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: vault_dir)

    out = mcp_server.es_doc_extract("$vault/../../../../../../etc/passwd")
    assert out["ok"] is False
    assert out["error"]["code"] == "doc_forbidden"
