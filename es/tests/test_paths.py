import pytest

from es import paths


def test_resolves_file_inside_allowed_root(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    f = root / "a.pdf"
    f.write_text("x")
    assert paths.resolve_readable(str(f), [root]) == f.resolve()


def test_rejects_file_outside_allowed_roots(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable(str(outside), [root])


def test_rejects_symlink_escaping_allowed_root(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    link = root / "link.txt"
    link.symlink_to(secret)
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable(str(link), [root])


def test_rejects_missing_file(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    with pytest.raises(paths.SourceNotFound):
        paths.resolve_readable(str(root / "nope.pdf"), [root])


def test_empty_roots_rejects_everything(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_text("x")
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable(str(f), [])


def test_rejects_sibling_prefix_collision(tmp_path):
    """A root of `cache` must not match a sibling `cache-evil` just because the
    string happens to start with the same prefix (guards against swapping
    is_relative_to for a naive str.startswith)."""
    root = tmp_path / "cache"
    root.mkdir()
    evil = tmp_path / "cache-evil"
    evil.mkdir()
    f = evil / "x.pdf"
    f.write_text("x")
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable(str(f), [root])


def test_allows_file_when_root_itself_is_a_symlink(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_dir)
    f = real_dir / "a.pdf"
    f.write_text("x")
    assert paths.resolve_readable(str(f), [link]) == f.resolve()


def test_none_roots_rejects_everything(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_text("x")
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable(str(f), None)


def test_rejects_directory_as_source(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    subdir = root / "adir"
    subdir.mkdir()
    with pytest.raises(paths.SourceNotFound):
        paths.resolve_readable(str(subdir), [root])
