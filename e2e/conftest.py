import os, socket, subprocess, time, tempfile
from pathlib import Path
import pytest, requests

IMAGE = os.environ.get("EVERSTONE_IMAGE", "everstone:dev")
CONTAINER_NAME = "everstone-e2e"

def _free_port():
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0)); return s.getsockname()[1]

def _write_config(cfg_path: Path, port: int) -> None:
    cfg_path.write_text(f"""\
public_url: https://e2e-test.example.com
name: Tester
agent:
  name: TestBot
  soul: "You are a test assistant."
couchdb:
  user: testuser
  password: testpass
  database: testvault
caldav:
  user: testcal
  password: testcalpass
livesync:
  passphrase: testphrase
obsidian:
  vault_name: testvault
telegram:
  owner_user_id: 123456
  bot_token: TEST_TOKEN
brave:
  api_key: test-brave-key-e2e
""")

@pytest.fixture(scope="session")
def everstone():
    data_dir = tempfile.mkdtemp(prefix="everstone-e2e-")
    port = _free_port()
    cfg_path = Path(data_dir) / "config.yaml"
    _write_config(cfg_path, port)

    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    subprocess.run([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{port}:80",
        "-v", f"{cfg_path}:/opt/config.yaml:ro",
        "-v", f"{data_dir}/data:/opt/data",
        IMAGE,
    ], check=True)

    base = f"http://localhost:{port}"
    for _ in range(60):
        try:
            r = requests.get(f"{base}/health", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        subprocess.run(["docker", "logs", CONTAINER_NAME])
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME])
        pytest.fail("Container did not become healthy in time")

    yield {"base_url": base, "container_name": CONTAINER_NAME, "data_dir": data_dir}

    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
