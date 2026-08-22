import json

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


# --- truncation: .txt / .md / .json -----------------------------------

def test_oversized_txt_truncates_at_a_line_boundary_with_a_marker(tmp_path):
    p = tmp_path / "big.txt"
    lines = [f"line {i:05d} of filler content to pad this out nicely" for i in range(5000)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    md, _ = doc_text.convert(p, tmp_path)

    assert "truncated" in md.lower()
    before_marker = md.split("\n\n*(truncated", 1)[0]
    kept_lines = before_marker.split("\n")
    # Every kept line matches a real original line exactly — proves the cut
    # landed on a line boundary, never mid-line (a partial line would not be
    # a member of the original list).
    assert all(line in lines for line in kept_lines)
    assert 0 < len(kept_lines) < len(lines)


def test_normal_size_txt_is_not_truncated(txt_file, tmp_path):
    md, _ = doc_text.convert(txt_file, tmp_path)
    assert "truncated" not in md.lower()


def test_normal_size_markdown_is_byte_identical_passthrough(tmp_path):
    p = tmp_path / "note.md"
    content = "# Title\n\nBody text.\n"
    p.write_text(content, encoding="utf-8")
    md, _ = doc_text.convert(p, tmp_path)
    assert md == content


def test_oversized_markdown_truncates_at_a_line_boundary_with_a_marker(tmp_path):
    p = tmp_path / "big.md"
    lines = [f"- item {i:05d} filler filler filler filler" for i in range(5000)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    md, _ = doc_text.convert(p, tmp_path)

    assert "truncated" in md.lower()
    before_marker = md.split("\n\n*(truncated", 1)[0]
    kept_lines = before_marker.split("\n")
    assert all(line in lines for line in kept_lines)
    assert 0 < len(kept_lines) < len(lines)


def test_oversized_json_truncates_and_closes_the_fence(tmp_path):
    p = tmp_path / "big.json"
    data = {"items": [{"id": i, "note": "x" * 50} for i in range(3000)]}
    p.write_text(json.dumps(data), encoding="utf-8")

    md, _ = doc_text.convert(p, tmp_path)

    assert "truncated" in md.lower()
    # Exactly one opening and one closing fence — a truncated fenced block
    # must never be left open, or the agent's Markdown renderer would treat
    # everything after it (including the marker) as still inside the block.
    assert md.count("```") == 2
    assert md.startswith("```json\n")
    closing_idx = md.rindex("```")
    marker_idx = md.index("*(truncated")
    assert marker_idx > closing_idx  # the marker sits OUTSIDE the fence


def test_normal_size_json_is_not_truncated(json_file, tmp_path):
    md, _ = doc_text.convert(json_file, tmp_path)
    assert "truncated" not in md.lower()
    assert md.count("```") == 2
