import pytest

from es.capabilities import doc_text


def test_csv_becomes_a_markdown_table(csv_file, tmp_path):
    md, images = doc_text.convert(csv_file, tmp_path)
    assert "| Name | Position | Number |" in md
    assert "| Alice | Forward | 9 |" in md
    assert images == []


def test_csv_escapes_pipes_in_cells(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\nfoo|bar,baz\n", encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    assert r"foo\|bar" in md


def test_json_is_pretty_printed_in_a_fenced_block(json_file, tmp_path):
    md, _ = doc_text.convert(json_file, tmp_path)
    assert "```json" in md
    assert '"team": "Thunder U10"' in md


def test_invalid_json_still_returns_the_raw_text(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    assert "not valid json" in md


def test_plain_text_passes_through(txt_file, tmp_path):
    md, _ = doc_text.convert(txt_file, tmp_path)
    assert "Practice moved to Thursday." in md


def test_markdown_passes_through_unchanged(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# Title\n\nBody text.\n", encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    assert md.strip() == "# Title\n\nBody text."


def test_undecodable_bytes_do_not_raise(tmp_path):
    """A .txt that isn't valid UTF-8 must degrade, not explode — these files
    come from the outside world."""
    p = tmp_path / "weird.txt"
    p.write_bytes(b"caf\xe9 \xff\xfe binary-ish")
    md, _ = doc_text.convert(p, tmp_path)
    assert isinstance(md, str) and md


def test_huge_csv_is_truncated_with_a_marker(tmp_path):
    p = tmp_path / "big.csv"
    rows = "\n".join(f"r{i},v{i}" for i in range(20_000))
    p.write_text("a,b\n" + rows + "\n", encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    assert "truncated" in md.lower()


def test_truncation_cuts_at_a_row_boundary(tmp_path):
    """Never leave a half-written table row — it reads as corrupt data."""
    p = tmp_path / "big.csv"
    rows = "\n".join(f"r{i},verylongvalue{i}" for i in range(20_000))
    p.write_text("a,b\n" + rows + "\n", encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    table_lines = [l for l in md.splitlines() if l.startswith("|")]
    assert all(l.rstrip().endswith("|") for l in table_lines)


def test_csv_field_with_embedded_newline_stays_one_row(tmp_path):
    """A quoted CSV field may legitimately contain a literal newline (RFC
    4180) — it must not be split into two table rows."""
    p = tmp_path / "multiline.csv"
    p.write_text('a,b\n"line one\nline two",baz\n', encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    assert "| line one line two | baz |" in md


def test_empty_file_does_not_raise(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    assert isinstance(md, str)
