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
