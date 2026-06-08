import subprocess

import requests


def test_health(everstone):
    r = requests.get(f"{everstone['base_url']}/health", timeout=5)
    assert r.status_code == 200 and r.text.strip() == "OK"


def test_caldav_reachable(everstone):
    # Radicale returns 302/207/401 depending on path — anything non-5xx means it's up
    r = requests.get(
        f"{everstone['base_url']}/caldav/", timeout=5, allow_redirects=False
    )
    assert 200 <= r.status_code < 500, f"got {r.status_code}: {r.text[:200]}"


def test_couchdb_reachable(everstone):
    r = requests.get(
        f"{everstone['base_url']}/db/",
        auth=("testuser", "testpass"),
        timeout=5,
    )
    assert r.status_code == 200 and "couchdb" in r.json()


def test_supervised_services_up(everstone):
    # caddy, couchdb, radicale, hermes, livesync-bridge are all under s6
    out = subprocess.run(
        ["docker", "exec", everstone["container_name"], "s6-rc", "-a", "list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for svc in ("caddy", "couchdb", "radicale", "hermes", "livesync-bridge"):
        assert svc in out, f"service {svc} not running: {out}"
