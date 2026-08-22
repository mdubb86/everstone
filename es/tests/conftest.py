import socket, subprocess, sys, time
import pytest, requests


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


@pytest.fixture
def radicale(tmp_path):
    storage = tmp_path / "collections"; storage.mkdir()
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "radicale", "--server-hosts", f"127.0.0.1:{port}",
         "--auth-type", "none", "--storage-filesystem-folder", str(storage),
         "--logging-level", "warning"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.request("OPTIONS", base + "/", timeout=0.5); break
        except requests.exceptions.ConnectionError:
            time.sleep(0.1)
    else:
        proc.terminate(); raise RuntimeError("Radicale did not start")
    yield base
    proc.terminate()
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired: proc.kill()


@pytest.fixture
def text_pdf(tmp_path):
    """Two-page PDF with a real text layer on both pages (plain drawString
    calls — no actual table; pdfplumber's extract_tables() finds none here)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    p = tmp_path / "text.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 720, "Fall Season Schedule")
    c.drawString(72, 700, "Team: Thunder U10")
    c.showPage()
    c.drawString(72, 720, "Game 1  Sat Sep 5  9:00 AM  Field 3")
    c.drawString(72, 700, "Game 2  Sat Sep 12  11:00 AM  Field 1")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def table_pdf(tmp_path):
    """One page with prose plus a genuinely bordered (reportlab GRID-style)
    table that pdfplumber's find_tables()/extract_tables() actually detect —
    confirmed empirically, not assumed. One cell contains a literal '|' to
    exercise pipe-escaping in the emitted Markdown table."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle

    p = tmp_path / "table.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 740, "Roster")
    data = [
        ["Name", "Position", "Number"],
        ["Alice", "Forward|Winger", "9"],
        ["Bob", "Goalie", "1"],
    ]
    t = Table(data, colWidths=[100, 100, 100])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    t.wrap(0, 0)
    t.drawOn(c, 72, 600)
    c.showPage()
    c.save()
    return p


@pytest.fixture
def table_only_pdf(tmp_path):
    """One page containing NOTHING but a bordered table — no heading, no
    other text anywhere on the page. Regression fixture for a blank-page
    test that (wrongly) decides on table-excluded text: that text would be
    empty here even though the page is far from blank."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle

    p = tmp_path / "table_only.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    data = [
        ["Name", "Position", "Number"],
        ["Alice", "Forward", "9"],
        ["Bob", "Goalie", "1"],
    ]
    t = Table(data, colWidths=[100, 100, 100])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    t.wrap(0, 0)
    t.drawOn(c, 72, 600)
    c.showPage()
    c.save()
    return p


@pytest.fixture
def mixed_content_pdf(tmp_path):
    """Two pages, each with meaningful text plus an image. Page 1's image is
    large (a chart) — the agent must be told it's there. Page 2's image is a
    small decorative mark (a logo) — it must NOT trigger a note on every page
    of an otherwise-textual document."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    big_png = tmp_path / "big.png"
    Image.new("RGB", (300, 200), (100, 150, 200)).save(big_png)
    small_png = tmp_path / "small.png"
    Image.new("RGB", (30, 30), (10, 10, 10)).save(small_png)

    p = tmp_path / "mixed.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 740, "Attendance chart for the fall season follows below.")
    c.drawImage(str(big_png), 72, 500, width=300, height=200)
    c.showPage()
    c.drawString(72, 740, "Report footer text with a small logo mark nearby.")
    c.drawImage(str(small_png), 72, 700, width=30, height=30)
    c.showPage()
    c.save()
    return p


@pytest.fixture
def scanned_pdf(tmp_path):
    """Single-page PDF with NO text layer — an image pasted onto the page."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    png = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), (200, 200, 200)).save(png)
    p = tmp_path / "scanned.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawImage(str(png), 72, 400, width=400, height=300)
    c.showPage()
    c.save()
    return p


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "roster.csv"
    p.write_text("Name,Position,Number\nAlice,Forward,9\nBob,Goalie,1\n", encoding="utf-8")
    return p


@pytest.fixture
def json_file(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"team": "Thunder U10", "games": [{"opp": "Fury"}, {"opp": "SC"}]}',
                 encoding="utf-8")
    return p


@pytest.fixture
def txt_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("Practice moved to Thursday.\nBring the blue kit.\n", encoding="utf-8")
    return p


@pytest.fixture
def ics_file(tmp_path):
    p = tmp_path / "schedule.ics"
    p.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:Game 1 vs Cedar Park Fury\r\n"
        "DTSTART:20260905T140000Z\r\nDTEND:20260905T153000Z\r\n"
        "LOCATION:Kelly Reeves Athletic Complex - Field 3\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:2\r\nSUMMARY:Game 2 vs Round Rock SC\r\n"
        "DTSTART:20260912T160000Z\r\nDTEND:20260912T173000Z\r\n"
        "LOCATION:Old Settlers Park - Field 1\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n", encoding="utf-8")
    return p
