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
