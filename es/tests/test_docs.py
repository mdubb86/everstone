import asyncio
import os
import re
import time

import pytest

from es.capabilities import docs


# --- fixtures for malformed/unusual PDFs -----------------------------------
# Kept local to this module (not conftest.py) since these exercise docs.py's
# own error-mapping, not doc_pdf's conversion logic.

@pytest.fixture
def encrypted_pdf(tmp_path):
    """A real password-protected PDF (reportlab supports this natively —
    no extra dependency needed)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.pdfgen import canvas

    p = tmp_path / "encrypted.pdf"
    enc = StandardEncryption("userpw", ownerPassword="ownerpw", canPrint=1)
    c = canvas.Canvas(str(p), pagesize=letter, encrypt=enc)
    c.drawString(72, 720, "secret")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def corrupt_pdf(tmp_path):
    """Has the %PDF magic bytes but no usable structure behind them."""
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"%PDF-1.4\n%truncated, no xref table, no /Root")
    return p


@pytest.fixture
def empty_pdf(tmp_path):
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    return p


@pytest.fixture
def fake_pdf(tmp_path):
    """A plain text file wearing a .pdf extension."""
    p = tmp_path / "fake.pdf"
    p.write_text("this is just a text file renamed to .pdf\n" * 5)
    return p


@pytest.fixture
def one_scanned_one_text_pdf(tmp_path):
    """Page 1 is image-only (auto-rendered by extract); page 2 has a real
    text layer. Gives extract() a non-empty images.json (from page 1) that
    is distinguishable from a PNG a LATER es_doc_render call would drop into
    the same artifact dir for page 2."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    png = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), (200, 200, 200)).save(png)
    p = tmp_path / "one_scanned_one_text.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawImage(str(png), 72, 400, width=400, height=300)
    c.showPage()
    c.drawString(72, 720, "Page two has a real text layer.")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def zero_page_pdf(tmp_path):
    """Opens fine as a PDF but legitimately has zero pages."""
    from reportlab.pdfgen import canvas

    p = tmp_path / "zero.pdf"
    canvas.Canvas(str(p)).save()
    return p


# --- extract() ---------------------------------------------------------

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
    assert "Fall Season Schedule" in cached.read_text(encoding="utf-8")


def test_extract_truncates_oversized_output_but_caches_the_full_markdown(
        text_pdf, tmp_path, monkeypatch):
    """Regression guard for writing the TRUNCATED copy to disk: this uses the
    SAME call/fixture for both assertions, so a future change that truncates
    before writing doc.md would fail here (a version that truncated first and
    used a different fixture for the doc.md check could pass both tests
    without ever proving the two stay in sync)."""
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", 40)
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert out["truncated"] is True
    # The content actually kept (everything before the appended resume
    # marker) stays bounded by the limit; the marker itself is allowed to
    # push the total past it (it's operator text, not document content).
    assert len(out["markdown"].rsplit("\n\n*(truncated", 1)[0]) <= 40

    cached = (tmp_path / ".es" / out["doc_id"] / "doc.md").read_text(encoding="utf-8")
    assert len(cached) > 40
    assert "Fall Season Schedule" in cached


def test_truncation_cuts_at_a_page_boundary_with_a_correct_usable_resume_range(
        text_pdf, tmp_path, monkeypatch):
    """When truncation lands past page 1, the cut must land exactly at the
    "## Page N" heading boundary (not mid-page), and the resume marker must
    name a page range that (a) picks up exactly where output stopped and
    (b) actually works if the agent uses it."""
    baseline = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert baseline["truncated"] is False
    boundary = baseline["markdown"].index("\n\n## Page 2")
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", boundary + 2)

    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert out["truncated"] is True
    assert out["markdown"].endswith(
        '*(truncated after page 1 of 2 — call es_doc_extract again with '
        'pages="2-2" to continue)*')
    # The cut landed cleanly at the page boundary: page 1's content survives
    # whole, page 2's content is entirely gone (not a partial fragment of it).
    assert "Fall Season Schedule" in out["markdown"]
    assert "Game 1" not in out["markdown"]

    # The marker's suggested range is actually usable.
    resumed = docs.extract(str(text_pdf), roots=[text_pdf.parent],
                           cache_root=tmp_path, pages="2-2")
    assert "Game 1" in resumed["markdown"]


def test_truncation_never_splits_a_markdown_image_link(tmp_path, monkeypatch):
    """Build a multi-page all-image PDF long enough that a naive
    markdown[:MAX_MARKDOWN_CHARS] slice would land inside one of the
    "![page N](/long/tmp/path/pNNN.png)" links; the fix must cut at a page
    boundary instead, so no link is ever left half-written."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    png = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), (200, 200, 200)).save(png)
    pdf = tmp_path / "all_scanned.pdf"
    c = canvas.Canvas(str(pdf), pagesize=letter)
    for _ in range(5):
        c.drawImage(str(png), 72, 400, width=400, height=300)
        c.showPage()
    c.save()

    baseline = docs.extract(str(pdf), roots=[tmp_path], cache_root=tmp_path)
    assert baseline["truncated"] is False
    link_start = baseline["markdown"].index("![page 3]")
    # Land the limit squarely inside page 3's image link.
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", link_start + 5)

    out = docs.extract(str(pdf), roots=[tmp_path], cache_root=tmp_path)
    assert out["truncated"] is True
    for m in re.finditer(r"!\[", out["markdown"]):
        tail = out["markdown"][m.start():]
        assert re.match(r"!\[[^\]]*\]\([^)]*\)", tail), \
            "an image link was cut in half by truncation"


def test_truncation_when_even_page_one_alone_exceeds_the_limit(text_pdf, tmp_path, monkeypatch):
    """No earlier page boundary exists to cut at, so this falls back to a hard
    cut. It must still say why (rather than silently truncating) and must not
    offer a page-range resume marker, since re-requesting page 1 alone would
    reproduce the identical oversized page."""
    monkeypatch.setattr(docs, "MAX_MARKDOWN_CHARS", 10)
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert out["truncated"] is True
    assert "page 1" in out["markdown"].lower()
    assert 'pages="' not in out["markdown"]


def test_extract_purges_stale_artifacts(text_pdf, tmp_path):
    from es import doc_cache
    stale = doc_cache.artifact_dir(tmp_path, "stale0000000")
    old = time.time() - (25 * 3600)
    os.utime(stale, (old, old))
    docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert not stale.exists()


def test_extract_narrows_to_requested_pages(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent],
                        cache_root=tmp_path, pages="1")
    assert out["page_count"] == 2  # total pages in the document
    assert "Fall Season Schedule" in out["markdown"]
    assert "Game 1" not in out["markdown"]  # page 2's content, excluded


def test_extract_second_call_is_a_cache_hit_not_a_reconvert(text_pdf, tmp_path, monkeypatch):
    calls = []
    original = docs.doc_pdf.convert

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(docs.doc_pdf, "convert", spy)

    first = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    second = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)

    assert len(calls) == 1  # convert() ran once; the second call was a cache hit
    assert second["markdown"] == first["markdown"]
    assert second["doc_id"] == first["doc_id"]


def test_extract_recovers_from_a_corrupted_cached_doc_md(text_pdf, tmp_path):
    """doc_id is a CONTENT hash: if doc.md was left truncated by a crash or
    ENOSPC, re-sending the identical PDF lands in the same artifact dir and
    must NOT raise (and must not stay broken for the 24h TTL) — an
    undecodable/unreadable doc.md must be treated as a cache miss, the same
    way a broken images.json already is, and the entry overwritten with a
    good reconversion."""
    first = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    md_path = tmp_path / ".es" / first["doc_id"] / "doc.md"
    md_path.write_bytes(b"\xff\xfe\x00 not valid utf-8 \x80\x81")

    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert "Fall Season Schedule" in out["markdown"]

    # The bad cache entry was overwritten with the good reconversion.
    assert "Fall Season Schedule" in md_path.read_text(encoding="utf-8")


def test_cache_hit_images_come_from_manifest_not_a_directory_scan(
        one_scanned_one_text_pdf, tmp_path):
    """A cache-hit extract must report only the images the ORIGINAL extract
    produced (from images.json) — not every PNG that happens to sit in the
    artifact dir, including ones a later es_doc_render call drops there for
    pages the extract itself never rendered."""
    first = docs.extract(str(one_scanned_one_text_pdf),
                         roots=[one_scanned_one_text_pdf.parent], cache_root=tmp_path)
    assert len(first["images"]) == 1  # only page 1, the image-only page

    # es_doc_render page 2 into the SAME artifact dir — page 2 has real text
    # and was never rendered by extract(), so its PNG is new to the dir.
    rendered = docs.render(str(one_scanned_one_text_pdf),
                           roots=[one_scanned_one_text_pdf.parent],
                           cache_root=tmp_path, pages="2")
    assert rendered["images"]
    adir = tmp_path / ".es" / first["doc_id"]
    assert len(list(adir.glob("*.png"))) == 2  # both PNGs now physically present

    second = docs.extract(str(one_scanned_one_text_pdf),
                          roots=[one_scanned_one_text_pdf.parent], cache_root=tmp_path)
    assert second["images"] == first["images"]  # unchanged by the render() call


def test_extract_page_subset_does_not_clobber_full_extract_cache(text_pdf, tmp_path):
    full = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    md_path = tmp_path / ".es" / full["doc_id"] / "doc.md"
    before = md_path.read_text(encoding="utf-8")

    subset = docs.extract(str(text_pdf), roots=[text_pdf.parent],
                           cache_root=tmp_path, pages="2")
    assert "Fall Season Schedule" not in subset["markdown"]  # page 1's content

    after = md_path.read_text(encoding="utf-8")
    assert after == before
    assert "Fall Season Schedule" in after  # still the whole document on disk


def test_extract_cache_hit_still_touches_artifact_dir(text_pdf, tmp_path):
    out = docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    adir = tmp_path / ".es" / out["doc_id"]
    old = time.time() - 1000
    os.utime(adir, (old, old))

    docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)

    assert os.stat(adir).st_mtime > old


def test_extract_rejects_oversized_document(text_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(docs.DocumentTooLarge) as e:
        docs.extract(str(text_pdf), roots=[text_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_too_large"


def test_extract_missing_file_mentions_resend(text_pdf, tmp_path):
    missing = text_pdf.parent / "gone.pdf"
    with pytest.raises(docs.paths.SourceNotFound) as e:
        docs.extract(str(missing), roots=[text_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_not_found"
    assert "resend" in str(e.value).lower() or "re-send" in str(e.value).lower()


def test_extract_encrypted_pdf_names_the_remedy(encrypted_pdf, tmp_path):
    with pytest.raises(docs.EncryptedDocument) as e:
        docs.extract(str(encrypted_pdf), roots=[encrypted_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_encrypted"
    assert "password" in str(e.value).lower()


def test_extract_corrupt_pdf_is_unreadable(corrupt_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(corrupt_pdf), roots=[corrupt_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_zero_byte_pdf_is_unreadable(empty_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(empty_pdf), roots=[empty_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_mislabeled_text_file_is_unreadable(fake_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(fake_pdf), roots=[fake_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_zero_page_pdf_is_unreadable(zero_page_pdf, tmp_path):
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(str(zero_page_pdf), roots=[zero_page_pdf.parent], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_rejects_over_long_path_without_leaking_oserror(tmp_path):
    """A path that resolves inside an allowed root but is too long for the
    filesystem must not surface as a raw OSError (which would leak the full
    path and an opaque 'OSError' code to the agent)."""
    root = tmp_path / "root"
    root.mkdir()
    source = str(root / ("a" * 5000 + ".pdf"))
    with pytest.raises(docs.UnreadableDocument) as e:
        docs.extract(source, roots=[root], cache_root=tmp_path)
    assert e.value.es_code == "doc_unreadable"


def test_extract_and_render_agree_on_empty_pages_string(text_pdf, tmp_path):
    """pages="" is a malformed selector in both tools, not a synonym for
    'whole document' — only omitting the argument (None) means that."""
    with pytest.raises(docs.InvalidPageRange):
        docs.extract(str(text_pdf), roots=[text_pdf.parent],
                     cache_root=tmp_path, pages="")
    with pytest.raises(docs.InvalidPageRange):
        docs.render(str(text_pdf), roots=[text_pdf.parent],
                    cache_root=tmp_path, pages="")


# --- render() ------------------------------------------------------------

def test_render_returns_page_images(text_pdf, tmp_path):
    out = docs.render(str(text_pdf), roots=[text_pdf.parent],
                      cache_root=tmp_path, pages="1-2")
    assert len(out["images"]) == 2
    assert out["page_count"] == 2


def _make_pdf(path, n_pages):
    """An n-page PDF with real text on every page (used to check the
    render() default range against documents of varying length)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(n_pages):
        c.drawString(72, 720, f"Page {i + 1} content")
        c.showPage()
    c.save()
    return path


@pytest.mark.parametrize("n_pages", [1, 2, 15])
def test_render_default_pages_clamps_to_the_document_length(tmp_path, n_pages):
    """The default (pages omitted) must never error just because the document
    is shorter than the default 1-10 window — the motivating case is a 1-3
    page schedule. A document at/over the window renders exactly the window."""
    pdf = _make_pdf(tmp_path / f"doc_{n_pages}.pdf", n_pages)
    out = docs.render(str(pdf), roots=[tmp_path], cache_root=tmp_path)
    assert out["page_count"] == n_pages
    assert len(out["images"]) == min(n_pages, 10)


def test_render_explicit_out_of_range_still_errors_even_when_default_would_clamp(tmp_path):
    """Clamping is only for the implicit default. An agent-supplied EXPLICIT
    range past the document's end stays a loud error (matching extract()'s
    explicit-range behavior) rather than silently returning a partial result —
    it more likely names a wrong page than an intentional partial ask."""
    pdf = _make_pdf(tmp_path / "doc_3.pdf", 3)
    with pytest.raises(docs.InvalidPageRange):
        docs.render(str(pdf), roots=[tmp_path], cache_root=tmp_path, pages="1-10")


def test_render_rejects_page_out_of_range(text_pdf, tmp_path):
    with pytest.raises(docs.InvalidPageRange):
        docs.render(str(text_pdf), roots=[text_pdf.parent],
                    cache_root=tmp_path, pages="9")


def test_render_rejects_more_pages_than_the_cap(text_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "MAX_RENDER_PAGES", 1)
    with pytest.raises(docs.InvalidPageRange) as e:
        docs.render(str(text_pdf), roots=[text_pdf.parent],
                    cache_root=tmp_path, pages="1-2")
    msg = str(e.value)
    # Specific enough to fail if the cap or the requested-page count regress
    # (the old "1" in str(e.value) assertion would pass on almost any message).
    assert "cannot render 2 pages" in msg
    assert "limit is 1" in msg


# --- parse_pages() ---------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("1", [1]), ("1-3", [1, 2, 3]), ("1-2,5", [1, 2, 5]), ("3,1", [1, 3]),
])
def test_parse_pages(spec, expected):
    assert docs.parse_pages(spec, page_count=10) == expected


def test_parse_pages_rejects_garbage():
    with pytest.raises(docs.InvalidPageRange):
        docs.parse_pages("one", page_count=10)


def test_parse_pages_rejects_reversed_range():
    with pytest.raises(docs.InvalidPageRange, match="reversed"):
        docs.parse_pages("3-1", page_count=3)


def test_parse_pages_rejects_negative_component():
    with pytest.raises(docs.InvalidPageRange, match="starting at 1"):
        docs.parse_pages("1--3", page_count=10)


def test_parse_pages_on_zero_page_document_names_the_document_not_a_range():
    with pytest.raises(docs.InvalidPageRange, match="no pages"):
        docs.parse_pages("1", page_count=0)


# --- MCP layer --------------------------------------------------------

def test_mcp_tools_are_registered():
    """hasattr() would still pass if @mcp.tool() were removed (the plain
    function is still an attribute of the module) — assert real registration
    with the server instead."""
    from es import mcp_server
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"es_doc_extract", "es_doc_render"} <= names


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


def test_mcp_render_returns_envelope_on_success(text_pdf, monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [text_pdf.parent])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_render(str(text_pdf), pages="1")
    assert out["ok"] is True
    assert len(out["data"]["images"]) == 1


def test_mcp_render_returns_envelope_on_bad_path(monkeypatch, tmp_path):
    from es import mcp_server
    monkeypatch.setattr(mcp_server, "_doc_roots", lambda: [tmp_path])
    monkeypatch.setattr(mcp_server, "_doc_cache_root", lambda: tmp_path)
    out = mcp_server.es_doc_render("/etc/passwd")
    assert out["ok"] is False
    assert out["error"]["code"] in ("doc_not_found", "doc_forbidden")


def test_doc_cache_root_follows_hermes_home(monkeypatch):
    from es import mcp_server
    monkeypatch.setenv("HERMES_HOME", "/somewhere/else")
    assert str(mcp_server._doc_cache_root()) == \
        "/somewhere/else/profiles/everstone/cache/documents"


def test_doc_roots_returns_media_cache_and_vault_only(tmp_path, monkeypatch):
    """_doc_roots() is the security-relevant seam deciding which directories
    are readable at all; exercise it against real config instead of
    monkeypatching it away, so a regression here (e.g. returning ["/"])
    actually fails a test."""
    from es import mcp_server

    cache_dir = tmp_path / "media-cache"
    vault_dir = tmp_path / "vault"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "obsidian:\n"
        "  vault_name: Vault\n"
        "  attachments:\n"
        f"    sources: [{cache_dir}]\n"
    )
    monkeypatch.setenv("ES_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("ES_VAULT_PATH", str(vault_dir))

    assert mcp_server._doc_roots() == [str(cache_dir), str(vault_dir)]


def test_forbidden_message_names_the_remedy(text_pdf, tmp_path):
    """docs._prepare wraps paths.SourceForbidden with a document-specific
    remedy, the same way it already wraps SourceNotFound — the generic
    paths.py message alone never tells the agent what IS readable."""
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(docs.paths.SourceForbidden) as e:
        docs.extract(str(text_pdf), roots=[outside], cache_root=tmp_path)
    msg = str(e.value).lower()
    assert "uploads" in msg or "vault" in msg
    assert e.value.es_code == "doc_forbidden"


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


# --- dispatch table ---------------------------------------------------------

def test_dispatch_table_covers_every_supported_extension():
    """SUPPORTED and the dispatch table must not drift apart — a format listed
    as supported with no converter would fail at call time, not import time."""
    from es.capabilities import docs
    assert set(docs.CONVERTERS) == docs.SUPPORTED


def test_unsupported_extension_names_the_supported_list(tmp_path):
    from es.capabilities import docs
    f = tmp_path / "x.rtf"
    f.write_text("x")
    with pytest.raises(docs.UnsupportedDocument) as e:
        docs.extract(str(f), roots=[tmp_path], cache_root=tmp_path)
    assert ".pdf" in str(e.value)
