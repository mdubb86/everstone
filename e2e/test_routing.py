import subprocess

import requests


def test_health(everstone):
    r = requests.get(f"{everstone['base_url']}/health", timeout=5)
    assert r.status_code == 200 and r.text.strip() == "OK"


def test_version(everstone):
    # /version is baked at build time (just build passes git describe + short sha)
    # and served as JSON. Values vary per build, so assert structure + non-empty.
    r = requests.get(f"{everstone['base_url']}/version", timeout=5)
    assert r.status_code == 200, r.status_code
    assert "application/json" in r.headers.get("Content-Type", ""), r.headers
    body = r.json()
    assert body.get("version"), body
    assert body.get("commit"), body


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


def test_root_redirects_to_webui_subpath(everstone):
    r = requests.get(f"{everstone['base_url']}/", timeout=5, allow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("Location", "").endswith("/hermes/"), r.headers.get("Location")


def test_unmatched_path_redirects_to_webui_subpath(everstone):
    # Old root bookmarks (any unmatched path) land in the UI rather than 404.
    r = requests.get(f"{everstone['base_url']}/nope", timeout=5, allow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("Location", "").endswith("/hermes/"), r.headers.get("Location")


def test_oauth_callback_not_swallowed_by_webui(everstone):
    # The OAuth callback must keep its own route (-> auth listener on :8081), NOT
    # fall into the /hermes/ catch-all. The auth listener isn't running in e2e, so
    # Caddy 502s and handle_errors returns the friendly 200 page; the key assertion
    # is that it is NOT a 302 redirect to /hermes/.
    r = requests.get(
        f"{everstone['base_url']}/oauth/google/callback",
        timeout=5, allow_redirects=False,
    )
    assert r.status_code != 302, "OAuth callback was redirected — route lost to catch-all"


def test_webui_subpath_proxied_not_redirected(everstone):
    # Web UI is disabled in e2e (no webui.password) -> /hermes/ 502s -> handle_errors
    # -> friendly "web UI not enabled" 200 page. The point: /hermes/ is PROXIED
    # (reaches the webui handler), not redirected, and not a raw gateway error.
    r = requests.get(
        f"{everstone['base_url']}/hermes/", timeout=5, allow_redirects=False,
    )
    assert r.status_code == 200, r.status_code
    assert "web UI not enabled" in r.text, r.text[:200]
