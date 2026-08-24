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
def photo_page_pdf(tmp_path):
    """One page: a short heading plus ONE embedded photo — the plain "a page
    has an image" case, distinct from scanned_pdf (which has no text layer
    at all)."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    photo = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), (100, 150, 200)).save(photo)

    p = tmp_path / "photo_page.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 740, "Team photo from the fall season banquet follows below.")
    c.drawImage(str(photo), 72, 500, width=300, height=200)
    c.showPage()
    c.save()
    return p


@pytest.fixture
def text_photo_text_pdf(tmp_path):
    """One page with a photo sandwiched between two distinguishable blocks
    of text — used to assert the image's Markdown link lands at its
    POSITION in the reading order, not appended after all the text."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    photo = tmp_path / "photo.png"
    Image.new("RGB", (300, 150), (100, 150, 200)).save(photo)

    p = tmp_path / "text_photo_text.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 740, "INTRO TEXT ABOVE THE PHOTO")
    c.drawImage(str(photo), 72, 500, width=300, height=150)
    c.drawString(72, 400, "OUTRO TEXT BELOW THE PHOTO")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def two_images_pdf(tmp_path):
    """One page with two distinct embedded photos at different vertical
    positions — both must be extracted to separate files and linked."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    top_photo = tmp_path / "top.png"
    Image.new("RGB", (200, 100), (200, 0, 0)).save(top_photo)
    bottom_photo = tmp_path / "bottom.png"
    Image.new("RGB", (200, 100), (0, 0, 200)).save(bottom_photo)

    p = tmp_path / "two_images.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 740, "Two photos on one page.")
    c.drawImage(str(top_photo), 72, 600, width=200, height=100)
    c.drawImage(str(bottom_photo), 72, 400, width=200, height=100)
    c.showPage()
    c.save()
    return p


@pytest.fixture
def image_on_second_page_pdf(tmp_path):
    """Two pages: page 1 is plain text with no image, page 2 has text plus
    one embedded photo — the image link must land in page 2's section only."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    photo = tmp_path / "photo.png"
    Image.new("RGB", (200, 150), (50, 200, 50)).save(photo)

    p = tmp_path / "image_on_second_page.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 720, "Page one has only text.")
    c.showPage()
    c.drawString(72, 720, "Page two has text and a photo.")
    c.drawImage(str(photo), 72, 500, width=200, height=150)
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


@pytest.fixture
def docx_file(tmp_path):
    from docx import Document
    p = tmp_path / "letter.docx"
    d = Document()
    d.add_heading("Season Overview", level=1)
    d.add_paragraph("Practices are Tuesdays and Thursdays.")
    d.add_heading("Fees", level=2)
    d.add_paragraph("Club dues are due Sep 1.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Item"; t.cell(0, 1).text = "Cost"
    t.cell(1, 0).text = "Kit";  t.cell(1, 1).text = "$65"
    d.save(str(p))
    return p


@pytest.fixture
def xlsx_file(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "roster.xlsx"
    wb = Workbook()
    ws = wb.active; ws.title = "Roster"
    ws.append(["Name", "Number"]); ws.append(["Alice", 9]); ws.append(["Bob", 1])
    ws2 = wb.create_sheet("Fees")
    ws2.append(["Item", "Cost"]); ws2.append(["Kit", 65])
    wb.save(str(p))
    return p
