import pytest

from es.capabilities import docs


def test_extract_returns_markdown_and_doc_id(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert len(out["doc_id"]) == 12
    assert out["kind"] == "pdf"
    assert out["page_count"] == 2
    assert "Fall Season Schedule" in out["markdown"]
    assert out["truncated"] is False


def test_extract_rejects_path_outside_roots(text_pdf, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(docs.paths.SourceForbidden):
        docs.extract(str(text_pdf), roots=[other], cache_root=tmp_path)


def test_extract_rejects_unsupported_extension(tmp_path):
    f = tmp_path / "notes.rtf"
    f.write_text("x")
    with pytest.raises(docs.UnsupportedDocument) as e:
        docs.extract(str(f), roots=[tmp_path], cache_root=tmp_path)
    assert ".rtf" in str(e.value)


def test_extract_writes_markdown_into_the_cache(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    cached = tmp_path / ".es" / out["doc_id"] / "doc.md"
    assert cached.is_file()
    assert "Fall Season Schedule" in cached.read_text()


def test_extract_truncates_oversized_output(text_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", 40)
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert out["truncated"] is True
    assert len(out["markdown"]) <= 40


def test_extract_purges_stale_artifacts(text_pdf, tmp_path):
    import os, time
    from es import doc_cache
    stale = doc_cache.artifact_dir(tmp_path, "stale0000000")
    old = time.time() - (25 * 3600)
    os.utime(stale, (old, old))
    docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert not stale.exists()


def test_render_returns_page_images(text_pdf, tmp_path):
    out = docs.render(str(text_pdf), roots=[text_pdf.parent],
                      cache_root=tmp_path, pages="1-2")
    assert len(out["images"]) == 2
    assert out["page_count"] == 2


def test_render_rejects_page_out_of_range(text_pdf, tmp_path):
    with pytest.raises(docs.InvalidPageRange):
        docs.render(str(text_pdf), roots=[text_pdf.parent],
                    cache_root=tmp_path, pages="9")


@pytest.mark.parametrize("spec,expected", [
    ("1", [1]), ("1-3", [1, 2, 3]), ("1-2,5", [1, 2, 5]), ("3,1", [1, 3]),
])
def test_parse_pages(spec, expected):
    assert docs.parse_pages(spec, page_count=10) == expected


def test_parse_pages_rejects_garbage():
    with pytest.raises(docs.InvalidPageRange):
        docs.parse_pages("one", page_count=10)


def test_extract_missing_file_mentions_resend(text_pdf, tmp_path):
    missing = text_pdf.parent / "gone.pdf"
    with pytest.raises(docs.paths.SourceNotFound) as e:
        docs.extract(str(missing), roots=[text_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_not_found"
    assert "resend" in str(e.value).lower() or "re-send" in str(e.value).lower()


def test_mcp_tools_are_registered():
    from es import mcp_server
    assert hasattr(mcp_server, "es_doc_extract")
    assert hasattr(mcp_server, "es_doc_render")


def test_mcp_extract_returns_envelope_on_bad_path(monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [tmp_path])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_extract("/etc/passwd")
    assert out["ok"] is False
    assert out["error"]["code"] in ("doc_not_found", "doc_forbidden")


def test_mcp_extract_returns_envelope_on_success(text_pdf, monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [text_pdf.parent])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_extract(str(text_pdf))
    assert out["ok"] is True
    assert "Fall Season Schedule" in out["data"]["markdown"]


def test_doc_cache_root_follows_hermes_home(monkeypatch):
    from es import mcp_server
    monkeypatch.setenv("HERMES_HOME", "/somewhere/else")
    assert str(mcp_server._doc_cache_root()) == \
        "/somewhere/else/profiles/everstone/cache/documents"


def test_forbidden_message_identical_whether_file_exists_or_not(text_pdf, tmp_path):
    """Confinement must fail the same way regardless of whether the forbidden
    path exists — otherwise the error becomes a path-probing oracle."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    # Both candidates sit outside the allowed root; one exists, one doesn't,
    # but they share a basename so the messages are comparable once that
    # shared name is substituted back in.
    existing = outside / "does-not-exist.pdf"
    existing.write_bytes(text_pdf.read_bytes())
    missing = outside / "does-not-exist-2.pdf"

    with pytest.raises(docs.paths.SourceForbidden) as e_exists:
        docs.extract(str(existing), roots=[allowed], cache_root=tmp_path)
    with pytest.raises(docs.paths.SourceForbidden) as e_missing:
        docs.extract(str(missing), roots=[allowed], cache_root=tmp_path)

    msg_exists = str(e_exists.value).replace(str(existing), "<PATH>")
    msg_missing = str(e_missing.value).replace(str(missing), "<PATH>")
    assert msg_exists == msg_missing
    assert "exist" not in msg_missing.lower()
