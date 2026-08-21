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
    """Two-page PDF with a real text layer; page 2 contains a table-like grid."""
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
