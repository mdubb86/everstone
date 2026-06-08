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
