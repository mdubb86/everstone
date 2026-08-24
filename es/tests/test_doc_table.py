"""Tests for doc_table.py — the .csv/.xlsx -> DuckDB converter.

Task 1's tests exercise the SERIALIZER directly rather than through DuckDB on
purpose: the sniffer's behaviour is a downstream consequence of the exact
field counts written here, so a failure at this level names the actual cause
("row 5 was written 4 fields wide") instead of the symptom DuckDB reports much
later and much more confusingly ("the whole file is one VARCHAR column").
"""
import datetime

import duckdb
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


def _xlsx(tmp_path, sheets, name="book.xlsx"):
    """`sheets` is an ordered {title: [row, ...]} mapping."""
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title=title)
        for r in rows:
            ws.append(r)
    p = tmp_path / name
    wb.save(p)
    return p


def _csv_file(tmp_path, text, name="data.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _by_sheet(result):
    return {t["sheet"]: t for t in result["tables"]}


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


# ---------------------------------------------------------------- build()


def test_a_csv_preamble_is_skipped_and_types_inferred(tmp_path):
    src = _csv_file(tmp_path, (
        "Quarterly Revenue Report\n"
        "generated 2026-08-24 by acme\n"
        "\n"
        "id,name,amount,when\n"
        "1,alice,10.5,2026-01-02\n"
        "2,bob,20.25,2026-01-03\n"
    ))
    adir = tmp_path / "art"
    adir.mkdir()
    result = doc_table.build(src, adir)

    assert (adir / doc_table.DB_NAME).is_file()
    t = result["tables"][0]
    assert [c["name"] for c in t["columns"]] == ["id", "name", "amount", "when"]
    assert [c["type"] for c in t["columns"]] == ["BIGINT", "VARCHAR", "DOUBLE", "DATE"]
    assert t["rows"] == 2
    # 1-based line number of the header row itself — three lines of preamble
    # precede it, so it is line 4. Stated as a LINE NUMBER (not a skip count)
    # so it can be cross-checked directly against the `cells` table, which is
    # also 1-based.
    assert t["header_row"] == 4


def test_a_headerless_csv_reports_no_header_row(tmp_path):
    src = _csv_file(tmp_path, "1,2,3\n4,5,6\n")
    adir = tmp_path / "art"
    adir.mkdir()
    t = doc_table.build(src, adir)["tables"][0]
    assert t["header_row"] is None
    assert [c["name"] for c in t["columns"]] == ["column0", "column1", "column2"]


def test_each_sheet_becomes_its_own_table_with_its_own_schema(tmp_path):
    src = _xlsx(tmp_path, {
        "Sales": [["id", "amount"], [1, 10.5], [2, 20.5]],
        "People": [["name", "city"], ["alice", "austin"]],
    })
    adir = tmp_path / "art"
    adir.mkdir()
    tables = _by_sheet(doc_table.build(src, adir))

    assert set(tables) == {"Sales", "People"}
    assert [c["type"] for c in tables["Sales"]["columns"]] == ["BIGINT", "DOUBLE"]
    assert [c["name"] for c in tables["People"]["columns"]] == ["name", "city"]
    assert tables["Sales"]["rows"] == 2
    assert tables["People"]["rows"] == 1


@pytest.mark.parametrize("title", [
    "Q1 Sales",           # spaces
    "2024 Budget",        # leading digit
    "order",              # a reserved SQL keyword
    "P&L (draft)",        # punctuation
    "Ventas Año",         # non-ASCII
])
def test_a_sheet_name_becomes_a_usable_table_name(tmp_path, title):
    """Whatever the mapping is, the name it produces must actually work as an
    unquoted identifier in the SQL the agent will write — that is the only
    property that matters here, so assert it by RUNNING a query rather than
    by pinning a particular slug."""
    src = _xlsx(tmp_path, {title: [["id"], [1]]})
    adir = tmp_path / "art"
    adir.mkdir()
    t = doc_table.build(src, adir)["tables"][0]

    assert t["sheet"] == title
    con = duckdb.connect(str(adir / doc_table.DB_NAME), read_only=True)
    try:
        assert con.execute(f"SELECT count(*) FROM {t['table']}").fetchone()[0] == 1
    finally:
        con.close()


def test_the_sheet_to_table_mapping_is_returned_not_inferred(tmp_path):
    """An identifier the agent reconstructs is one it gets wrong. Two sheets
    that slugify to the same name must still be distinguishable, and the
    receipt has to say which is which."""
    src = _xlsx(tmp_path, {
        "Q1 Sales": [["id"], [1]],
        "Q1-Sales": [["id"], [2], [3]],
    })
    adir = tmp_path / "art"
    adir.mkdir()
    tables = _by_sheet(doc_table.build(src, adir))

    names = [t["table"] for t in tables.values()]
    assert len(set(names)) == 2, "deduplication must not collapse two sheets"
    con = duckdb.connect(str(adir / doc_table.DB_NAME), read_only=True)
    try:
        for sheet, expect in (("Q1 Sales", 1), ("Q1-Sales", 2)):
            got = con.execute(
                f"SELECT count(*) FROM {tables[sheet]['table']}").fetchone()[0]
            assert got == expect, f"{sheet} maps to the wrong table"
    finally:
        con.close()


def test_an_empty_sheet_is_a_table_with_zero_rows_not_an_error(tmp_path):
    src = _xlsx(tmp_path, {"Blank": [], "Real": [["id"], [1]]})
    adir = tmp_path / "art"
    adir.mkdir()
    tables = _by_sheet(doc_table.build(src, adir))

    assert tables["Blank"]["rows"] == 0
    con = duckdb.connect(str(adir / doc_table.DB_NAME), read_only=True)
    try:
        assert con.execute(
            f"SELECT count(*) FROM {tables['Blank']['table']}").fetchone()[0] == 0
    finally:
        con.close()


def test_a_large_sheet_converts_in_full_with_no_row_cap(tmp_path):
    """The bug that started this: 40,000 rows of spreadsheet became a capped
    Markdown table, and the agent confidently answered a count question with
    the size of the cap."""
    from openpyxl import Workbook as WB
    wb = WB(write_only=True)
    ws = wb.create_sheet(title="Txns")
    ws.append(["id", "amount"])
    for i in range(1, 40001):
        ws.append([i, float(i)])
    src = tmp_path / "big.xlsx"
    wb.save(src)

    adir = tmp_path / "art"
    adir.mkdir()
    t = doc_table.build(src, adir)["tables"][0]
    assert t["rows"] == 40000

    con = duckdb.connect(str(adir / doc_table.DB_NAME), read_only=True)
    try:
        assert con.execute(f"SELECT count(*) FROM {t['table']}").fetchone()[0] == 40000
        assert con.execute(
            f"SELECT sum(amount) FROM {t['table']}").fetchone()[0] == 800020000.0
    finally:
        con.close()


def test_rebuilding_over_an_existing_database_replaces_it(tmp_path):
    """The artifact directory is keyed by a content hash, so a rebuild always
    means the same content — but a half-written database left by a crashed
    conversion must not be appended to or reused."""
    adir = tmp_path / "art"
    adir.mkdir()
    src = _xlsx(tmp_path, {"Sales": [["id"], [1]]})
    doc_table.build(src, adir)
    t = doc_table.build(src, adir)["tables"][0]

    con = duckdb.connect(str(adir / doc_table.DB_NAME), read_only=True)
    try:
        assert con.execute(f"SELECT count(*) FROM {t['table']}").fetchone()[0] == 1
    finally:
        con.close()
