"""Tests for doc_table.py — the .csv/.xlsx -> DuckDB converter.

Task 1's tests exercise the SERIALIZER directly rather than through DuckDB on
purpose: the sniffer's behaviour is a downstream consequence of the exact
field counts written here, so a failure at this level names the actual cause
("row 5 was written 4 fields wide") instead of the symptom DuckDB reports much
later and much more confusingly ("the whole file is one VARCHAR column").
"""
import datetime

import openpyxl
import pytest
from openpyxl import Workbook

from es.capabilities import doc_table


def _sheet(rows, title="Data", merges=()):
    """Build a real .xlsx in memory and hand back a READ-ONLY worksheet — the
    same mode doc_table uses in production. Round-tripping through an actual
    file matters: read-only mode's row padding comes from the workbook's
    declared dimension, which only exists once openpyxl has written and
    reparsed the sheet. A hand-built in-memory worksheet would pad
    differently and quietly test something else."""
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    for m in merges:
        ws.merge_cells(m)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    wb2 = openpyxl.load_workbook(buf, read_only=True, data_only=True)
    return wb2[title]


def _lines(ws, tmp_path, name="out.csv"):
    p = tmp_path / name
    doc_table.sheet_to_csv(ws, p)
    return p.read_text(encoding="utf-8").splitlines()


def test_a_banner_row_is_written_as_one_field(tmp_path):
    ws = _sheet([
        ["Quarterly Revenue Report"],
        ["id", "name", "amount"],
        [1, "alice", 10.5],
    ])
    lines = _lines(ws, tmp_path)
    # One field, not "Quarterly Revenue Report,," — the column-count contrast
    # against the 3-field rows below is the ONLY signal DuckDB's sniffer has
    # that this line is a preamble and not data.
    assert lines[0] == "Quarterly Revenue Report"
    assert lines[1] == "id,name,amount"


def test_a_data_row_with_an_empty_last_cell_keeps_full_width(tmp_path):
    """The regression guard for the bug that collapsed an entire file into a
    single column: trimming trailing empties from EVERY row made this row 2
    fields where its neighbours had 3, and the sniffer then read the whole
    file as one VARCHAR column — silently, with no error anywhere."""
    ws = _sheet([
        ["id", "name", "note"],
        [1, "alice", "first"],
        [2, "bob", None],
        [3, "carol", "third"],
    ])
    lines = _lines(ws, tmp_path)
    assert lines[2] == "2,bob,"
    assert all(len(ln.split(",")) == 3 for ln in lines)


def test_a_blank_row_is_a_blank_line(tmp_path):
    ws = _sheet([
        ["Report"],
        [],
        ["id", "name"],
        [1, "alice"],
    ])
    lines = _lines(ws, tmp_path)
    assert lines[1] == ""


def test_a_merged_cell_banner_behaves_like_a_banner(tmp_path):
    """A real spreadsheet's title is usually merged across the data columns.
    openpyxl reports the merge's value in the top-left cell and None in the
    rest, so it arrives here as an ordinary one-non-empty-cell row — this
    pins that it is not accidentally padded to full width by the merge."""
    ws = _sheet([
        ["Quarterly Revenue Report", None, None],
        ["id", "name", "amount"],
        [1, "alice", 10.5],
    ], merges=("A1:C1",))
    lines = _lines(ws, tmp_path)
    assert lines[0] == "Quarterly Revenue Report"


def test_a_single_column_sheet_is_not_mangled(tmp_path):
    """The width > 1 guard. In a genuinely one-column sheet EVERY row has
    exactly one non-empty cell — treating each as a banner would be
    harmless-looking and completely wrong, since there is no wider data row
    for them to contrast against."""
    ws = _sheet([["name"], ["alice"], ["bob"]])
    lines = _lines(ws, tmp_path)
    assert lines == ["name", "alice", "bob"]


def test_dates_serialize_as_iso_and_none_as_empty(tmp_path):
    ws = _sheet([
        ["id", "when", "note"],
        [1, datetime.datetime(2026, 1, 2), None],
        [2, datetime.datetime(2026, 1, 3, 14, 30), "later"],
    ])
    lines = _lines(ws, tmp_path)
    # A date-formatted cell comes back from openpyxl as a datetime at
    # midnight — writing it as a bare date keeps DuckDB's sniffer inferring
    # DATE rather than TIMESTAMP for what the spreadsheet showed as a date.
    assert lines[1] == "1,2026-01-02,"
    assert lines[2] == "2,2026-01-03 14:30:00,later"


def test_a_value_containing_the_delimiter_is_quoted(tmp_path):
    ws = _sheet([
        ["id", "note"],
        [1, "hello, world"],
        [2, 'she said "hi"'],
    ])
    lines = _lines(ws, tmp_path)
    assert lines[1] == '1,"hello, world"'
    assert lines[2] == '2,"she said ""hi"""'


def test_an_empty_sheet_writes_nothing(tmp_path):
    ws = _sheet([])
    p = tmp_path / "empty.csv"
    doc_table.sheet_to_csv(ws, p)
    assert p.read_text(encoding="utf-8") == ""
