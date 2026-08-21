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


def test_scalar_string_root_is_not_splatted_into_characters(tmp_path):
    """A bare string root must behave like a single-element list, not an
    iterable of characters — `roots="/x"` must NOT decompose into `Path("/")`,
    which would contain every path on the filesystem."""
    root = tmp_path / "cache"
    root.mkdir()
    inside = root / "a.pdf"
    inside.write_text("x")
    assert paths.resolve_readable(str(inside), str(root)) == inside.resolve()

    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable(str(outside), str(root))


def test_symlink_loop_is_forbidden_not_not_found(tmp_path):
    """An unresolvable path (symlink loop) must fail the same way as any other
    forbidden path — we cannot prove containment, so it is not distinguishable
    from a plain forbidden path (closes the probing oracle for this case)."""
    root = tmp_path / "cache"
    root.mkdir()
    loop_a = tmp_path / "a"
    loop_b = tmp_path / "b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable(str(loop_a), [root])


def test_embedded_nul_byte_is_forbidden(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    with pytest.raises(paths.SourceForbidden):
        paths.resolve_readable("/tmp/a\0b", [root])


def test_forbidden_error_is_identical_whether_or_not_file_exists(tmp_path):
    """Pins the closed oracle: a path outside the allowlist must raise the same
    exception type AND message regardless of whether it exists on disk, so a
    future refactor can't reopen the exists-vs-forbidden distinction."""
    root = tmp_path / "cache"
    root.mkdir()
    exists = tmp_path / "secret.txt"
    exists.write_text("x")
    missing = tmp_path / "nope.txt"

    with pytest.raises(paths.SourceForbidden) as exists_exc:
        paths.resolve_readable(str(exists), [root])
    with pytest.raises(paths.SourceForbidden) as missing_exc:
        paths.resolve_readable(str(missing), [root])

    assert type(exists_exc.value) is type(missing_exc.value)
    assert exists_exc.value.es_code == missing_exc.value.es_code
    # Messages differ only in the echoed path, not in structure/wording.
    assert str(exists_exc.value).replace(str(exists), "PATH") == \
        str(missing_exc.value).replace(str(missing), "PATH")
