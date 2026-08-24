"""`.csv`/`.xlsx` -> a queryable DuckDB database.

These two formats stop being documents here. A spreadsheet converted to a
Markdown pipe table is a document the agent has to READ — 40,000 rows of it,
one window at a time, to answer "how many transactions over $500 in
September". As a table it is one `SELECT count(*) ... WHERE` returning one
row. The agent writes competent SQL out of the box, so the useful thing to
hand it is a database, not prose about a database.

Both formats reach DuckDB through the SAME door: its CSV sniffer. `.xlsx` is
read by openpyxl and serialized to CSV first rather than going through
DuckDB's own `read_xlsx`, which was measured and rejected — given a sheet
with an ordinary title banner it returned ONE column, named after the banner,
containing ZERO rows. The sniffer, by contrast, needed no configuration at
all for the same shape: it reported `SkipRows: 4` and inferred
`BIGINT/VARCHAR/DOUBLE/DATE` correctly. One code path for both formats also
means header detection, type inference and preamble handling are the same
machinery, tested once.

**The serialization rule below is load-bearing — do not "tidy" it.** The
sniffer identifies a preamble purely by COLUMN-COUNT CONTRAST: a line with
fewer fields than the block beneath it is not data. So:

    blank row                        -> a blank line
    exactly one non-empty cell       -> ONE field
    anything else                    -> PADDED to the full sheet width

An earlier version trimmed trailing empty cells from every row. That made a
data row whose last column happened to be empty 4 fields wide where its
neighbours were 5 — and DuckDB then read the entire file as a SINGLE VARCHAR
column. Nothing raised, nothing warned; the data was simply wrong.
"""
import csv
import datetime
from pathlib import Path
from typing import Optional


def _cell_text(value) -> str:
    """One spreadsheet cell as the text CSV should carry.

    Dates are the case worth explaining. openpyxl hands back a
    `datetime.datetime` for BOTH a date-formatted cell and a real timestamp —
    Excel stores one numeric type for both and the distinction lives only in
    the number format. A date-formatted cell therefore arrives as midnight,
    and writing it out as `2026-01-02 00:00:00` makes DuckDB infer TIMESTAMP
    for a column the spreadsheet displayed as dates. Emitting the bare date
    when the time component is exactly midnight recovers the DATE the user
    actually sees. The cost is a genuine timestamp that happens to land on
    midnight losing a `00:00:00` it can be read back as anyway — DuckDB parses
    a bare date into a TIMESTAMP column without complaint, so a column mixing
    both still comes out right.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        # Before the int check: bool IS an int in Python, and "True" is not a
        # value DuckDB reads back as BOOLEAN as reliably as "true".
        return "true" if value else "false"
    if isinstance(value, datetime.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _sheet_width(ws) -> int:
    """The full data width every non-banner row is padded to.

    `ws.max_column` comes from the workbook's DECLARED dimension, and in
    read-only mode that same declaration is what makes openpyxl pad each row
    it yields to a uniform length — so the two agree by construction and the
    padding below is normally a no-op safety net. It stays anyway for the
    workbook whose declared dimension is too SMALL: a row longer than `width`
    is written in full (the negative padding count degrades to no padding
    rather than truncating), because losing a column of real data would be
    far worse than the ragged row the sniffer might then misread.

    Do NOT call `ws.reset_dimensions()` to "fix" a suspect declaration: it
    makes iter_rows yield RAGGED rows (measured: a 5-column sheet gave rows
    of 1, 0, 5, 5, 4, 5), which is exactly the trailing-empty asymmetry the
    module docstring's rule exists to prevent.
    """
    width = ws.max_column
    return width if isinstance(width, int) and width > 0 else 1


def sheet_to_csv(ws, path: Path) -> int:
    """Serialize one worksheet to `path` per the module docstring's rule.
    Returns the number of rows written.

    Reads with `data_only=True`, which returns Excel's CACHED formula results
    — so a workbook written by a tool that never opened Excel (a report
    exported by a web app, most commonly) has no cache and its formula cells
    arrive EMPTY. There is no fix at this layer: the formula's result does
    not exist in the file. The `cells` table is the backstop — it records
    what each cell actually held, so a column of blanks is at least
    diagnosable rather than silently zero.

    Trailing blank rows are dropped rather than written. An .xlsx routinely
    declares a dimension larger than its real data (openpyxl then pads out
    the difference), and hundreds of empty lines at the end of the file are
    noise the sniffer has to reason past. Blank rows BETWEEN data are kept —
    those are structure, often the very gap that separates a preamble from
    its table.
    """
    width = _sheet_width(ws)
    written = 0
    pending_blanks = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        # "\n" rather than the dialect default "\r\n" so a blank row is one
        # byte and every line ends the same way, whichever branch wrote it.
        writer = csv.writer(fh, lineterminator="\n")
        for row in ws.iter_rows(values_only=True):
            cells = [_cell_text(v) for v in row]
            non_empty = [c for c in cells if c != ""]
            if not non_empty:
                # Held back, not written: if nothing follows, these are the
                # sheet's trailing padding and belong nowhere.
                pending_blanks += 1
                continue
            for _ in range(pending_blanks):
                writer.writerow([])
                written += 1
            pending_blanks = 0
            if len(non_empty) == 1 and width > 1:
                writer.writerow([non_empty[0]])
            else:
                writer.writerow(cells + [""] * (width - len(cells)))
            written += 1
    return written
