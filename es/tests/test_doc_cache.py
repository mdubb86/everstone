import os
import time
from pathlib import Path

from es import doc_cache


def test_doc_id_is_stable_for_same_content(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"hello")
    b.write_bytes(b"hello")
    assert doc_cache.doc_id(a) == doc_cache.doc_id(b)


def test_doc_id_differs_for_different_content(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"hello")
    b.write_bytes(b"world")
    assert doc_cache.doc_id(a) != doc_cache.doc_id(b)


def test_doc_id_is_short_hex(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"hello")
    did = doc_cache.doc_id(a)
    assert len(did) == 12 and all(c in "0123456789abcdef" for c in did)


def test_artifact_dir_is_created_under_es_namespace(tmp_path):
    d = doc_cache.artifact_dir(tmp_path, "ab12cd34ef56")
    assert d == tmp_path / ".es" / "ab12cd34ef56"
    assert d.is_dir()


def test_page_image_path_is_zero_padded(tmp_path):
    d = doc_cache.artifact_dir(tmp_path, "ab12cd34ef56")
    assert doc_cache.page_image_path(d, 3).name == "p003.png"


def test_purge_removes_stale_directories(tmp_path):
    stale = doc_cache.artifact_dir(tmp_path, "stale0000000")
    (stale / "doc.md").write_text("x")
    old = time.time() - (25 * 3600)
    os.utime(stale, (old, old))
    removed = doc_cache.purge(tmp_path)
    assert removed == 1
    assert not stale.exists()


def test_purge_keeps_fresh_directories(tmp_path):
    fresh = doc_cache.artifact_dir(tmp_path, "fresh0000000")
    (fresh / "doc.md").write_text("x")
    assert doc_cache.purge(tmp_path) == 0
    assert fresh.exists()


def test_touch_makes_a_stale_directory_fresh(tmp_path):
    d = doc_cache.artifact_dir(tmp_path, "touched00000")
    old = time.time() - (25 * 3600)
    os.utime(d, (old, old))
    doc_cache.touch(d)
    assert doc_cache.purge(tmp_path) == 0
    assert d.exists()


def test_purge_ignores_hermes_inbound_files(tmp_path):
    """Regression note: with no `.es/` namespace directory at all, purge()
    short-circuits on `ns.is_dir()` before it could ever touch anything —
    so this test alone would pass even if purge recursed into and deleted
    namespace files. Create a real (stale) namespace entry alongside the
    inbound file so purge actually runs its removal logic here, and pin the
    real "doesn't touch the parent dir" coverage in
    test_purge_survives_a_file_in_the_namespace / …stale_directories."""
    inbound = tmp_path / "inbound.pdf"
    inbound.write_bytes(b"x")
    old = time.time() - (25 * 3600)
    os.utime(inbound, (old, old))

    stale = doc_cache.artifact_dir(tmp_path, "stale0000000")
    os.utime(stale, (old, old))

    removed = doc_cache.purge(tmp_path)

    assert removed == 1
    assert not stale.exists()
    assert inbound.exists(), "purge must not touch Hermes's own cache files"


def test_purge_on_missing_namespace_is_noop(tmp_path):
    assert doc_cache.purge(tmp_path) == 0


def test_doc_id_matches_known_sha256(tmp_path):
    """doc_id folds the (lowercased) extension into the hash ahead of the
    content, separated by a NUL byte — see doc_cache.doc_id's docstring for
    why the format must be part of the identity, not just the bytes."""
    import hashlib
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    expected = hashlib.sha256(b".pdf\0hello").hexdigest()[:12]
    assert doc_cache.doc_id(f) == expected


def test_doc_id_differs_for_same_content_different_extension(tmp_path):
    """The cross-format cache collision this fixes: identical bytes saved
    under two different extensions must hash to two different ids, since a
    .csv reading and a .pdf reading of the same bytes are different, both
    independently correct, documents (different kind/markdown/page_count) —
    not "the same document twice"."""
    a = tmp_path / "a.csv"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"same bytes")
    b.write_bytes(b"same bytes")
    assert doc_cache.doc_id(a) != doc_cache.doc_id(b)


def test_doc_id_differs_for_zero_byte_files_of_different_extension(tmp_path):
    """Regression guard named in the review: two empty files of different
    extensions must not collide either — an empty-content edge case that a
    pure content hash (with nothing to distinguish) would otherwise unify."""
    a = tmp_path / "a.csv"
    b = tmp_path / "b.txt"
    a.write_bytes(b"")
    b.write_bytes(b"")
    assert doc_cache.doc_id(a) != doc_cache.doc_id(b)


def test_doc_id_ext_param_overrides_the_path_suffix(tmp_path):
    """doc_id(source, ext=...) lets a caller name the format explicitly
    (docs.py does, using the already-lowercased `ext` it resolved) rather
    than re-deriving it from source.suffix — confirm the explicit value
    actually wins and is lowercase-sensitive the same way."""
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    assert doc_cache.doc_id(f, ext=".csv") != doc_cache.doc_id(f, ext=".pdf")
    assert doc_cache.doc_id(f, ext=".pdf") == doc_cache.doc_id(f)  # matches the path's own suffix


def test_artifact_dir_is_idempotent(tmp_path):
    a = doc_cache.artifact_dir(tmp_path, "ab12cd34ef56")
    (a / "doc.md").write_text("x")
    b = doc_cache.artifact_dir(tmp_path, "ab12cd34ef56")
    assert a == b
    assert (b / "doc.md").read_text() == "x", "must not clobber existing artifacts"


def test_purge_survives_a_file_in_the_namespace(tmp_path):
    ns = tmp_path / ".es"
    ns.mkdir(parents=True, exist_ok=True)
    stray = ns / "stray.txt"
    stray.write_text("x")
    old = time.time() - (25 * 3600)
    os.utime(stray, (old, old))
    assert doc_cache.purge(tmp_path) == 0, "only directories are purged"
    assert stray.exists()


def test_purge_skips_symlinks_without_counting_them(tmp_path):
    """rmtree refuses to act through a symlink, so counting one as removed
    would overstate the result. Nothing creates these, but don't lie if present."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    ns = tmp_path / ".es"
    ns.mkdir(parents=True, exist_ok=True)
    link = ns / "linked000000"
    link.symlink_to(outside)
    old = time.time() - (25 * 3600)
    os.utime(link, (old, old), follow_symlinks=False)

    assert doc_cache.purge(tmp_path) == 0
    assert (outside / "keep.txt").exists(), "must never delete through a symlink"
