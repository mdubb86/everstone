"""The `/web-login/` noVNC pipe, driven for real: WS → Caddy → websockify → x11vnc → RFB.

This module exists because unit tests could not have caught the bug it guards. `es_login`
swaps the `/web-login/` Caddy block from a static "preparing" page to `reverse_proxy
127.0.0.1:6080` once a background thread decides the VNC server is up. That decision used to
be "the first probe passed" — but the vnc plugin's watcher RESTARTS x11vnc when Camoufox's
display changes, and x11vnc exits when its X server goes away, so :5900 can serve, drop, and
come back during browser startup. Arming inside that gap published a noVNC link that could not
connect, and noVNC's autoconnect never retries a FIRST connect (it starts with
`inhibitReconnect = true` and only clears it after a successful connect), so the operator got
a dead-end "Failed to connect to server."

The invariant these tests assert is the user-facing one, and it is stronger than "it worked
once": **whenever `/web-login/` serves noVNC, a fresh client must be able to complete the RFB
handshake through Caddy on its FIRST try.** The tests sample the route continuously through the
whole open→armed window, so a premature arm is caught at the instant it happens rather than
being papered over by a later retry.

Holding that invariant over TIME is a second, sharper problem — see
test_an_open_login_window_outlives_the_idle_reapers. camofox reaps what looks idle to it, and a
human signing in by hand is invisible to its API, so an armed window used to lose its browser
out from under it minutes after opening. That one reproduces without any race at all.
"""

import base64
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

import conftest


AUTH = "http://localhost:9378"
VENV_PY = "/usr/local/lib/hermes-agent/.venv/bin/python"
PROFILE = "e2e-vnc"


def _exec(container, *args, timeout=180):
    return subprocess.run(["docker", "exec", container, *args],
                          capture_output=True, text=True, timeout=timeout)


def _py(container, code, detach=False):
    """Run a snippet in the hermes venv (where es lives). `detach` for code that must outlive
    the call — the arm runs on a background thread, so the process has to stay alive for it."""
    flags = ["-d"] if detach else []
    return subprocess.run(["docker", "exec", *flags, container, VENV_PY, "-c", code],
                          capture_output=True, text=True, timeout=180)


def _auth_health(container):
    r = _exec(container, "curl", "-s", "-m", "10", f"{AUTH}/health")
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


def _wait_browser(container, timeout_s=180):
    """Poll until Camoufox has actually launched behind the REST server (see
    test_browser_isolation: /health goes ok:true well before the browser is up)."""
    last = None
    for _ in range(timeout_s // 2):
        last = _auth_health(container)
        if last.get("browserRunning") and last.get("browserConnected"):
            return last
        time.sleep(2)
    raise AssertionError(f"{AUTH} browser never came up; last /health: {last}")


# --- the real client path -----------------------------------------------------------------

def rfb_over_ws(host, port, path="/web-login/websockify", timeout=6):
    """Do what noVNC does: RFC 6455 upgrade through Caddy, then read the server's first frame.

    Dependency-free on purpose — the point is to be a foreign client of the real ingress, not
    to reuse anything from es. Returns (http_status_line, first_12_payload_bytes).
    """
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
           "Sec-WebSocket-Protocol: binary\r\n\r\n")
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        s.settimeout(timeout)
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                return "closed-before-handshake", b""
            buf += chunk
        head, rest = buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n")[0].decode()
        if " 101 " not in status:
            return status, b""
        data = rest
        while len(data) < 2:
            chunk = s.recv(4096)
            if not chunk:
                return status, b""
            data += chunk
        ln, i = data[1] & 0x7F, 2
        if ln == 126:
            while len(data) < 4:
                data += s.recv(4096)
            ln, i = int.from_bytes(data[2:4], "big"), 4
        payload = data[i:]
        while len(payload) < min(ln, 12):
            chunk = s.recv(4096)
            if not chunk:
                break
            payload += chunk
        return status, payload[:12]
    finally:
        s.close()


def _rfb_ok(base_url):
    """True iff a brand-new client completes the WS upgrade AND gets an RFB banner."""
    u = urlparse(base_url)
    try:
        status, payload = rfb_over_ws(u.hostname, u.port or 80)
    except OSError as e:
        return False, f"socket: {e}"
    if " 101 " not in status:
        return False, f"no upgrade: {status}"
    if not payload.startswith(b"RFB "):
        return False, f"no RFB banner: {payload!r}"
    return True, payload.decode(errors="replace").strip()


def _route_state(base_url):
    """Which of the three /web-login/ states Caddy is serving right now."""
    try:
        body = requests.get(f"{base_url}/web-login/vnc.html", timeout=5).text
    except requests.RequestException as e:
        return f"error:{type(e).__name__}"
    if "noVNC" in body:
        return "armed"
    if "Preparing your login" in body:
        return "preparing"
    if "Login window isn't active" in body:
        return "closed"
    return "unknown"


# --- driving a login window ---------------------------------------------------------------

def _open_signin(container):
    """Arm a login window, exactly as es_login does — same open_signin(), same background arm.

    Only the destination is swapped: the real one is accounts.google.com, and an e2e box must
    not depend on reaching Google (or on Google's page weight) to test a local VNC pipe. The
    browser launch, the display, the watcher and the arm are all identical.
    """
    _py(container, (
        "import time, es.web_login as w\n"
        "w._SIGNIN_URL = 'http://localhost/health'\n"
        f"w.open_signin({PROFILE!r})\n"
        # es runs inside the long-lived MCP server, so its background arm and keep-alive threads
        # outlive the call. Stand in for that here — _kill_arms tears it down between tests.
        "time.sleep(900)\n"
    ), detach=True)


def _close_window(container):
    _py(container, "import es.web_login as w; w.close_window()")


def _kill_the_display(container):
    """Kill the authenticated browser's Xvfb — the honest way to reproduce a dead noVNC pipe.

    Killing Camoufox alone does NOT do it: Xvfb belongs to the camofox server, not the browser,
    so it survives and x11vnc happily keeps serving an empty desktop. Losing the DISPLAY is what
    actually takes the pipe down (x11vnc exits with its X server), and it is what camofox's own
    teardown does when it closes the browser fully. Matched on the 1920x1080 geometry so flex's
    own 1280x720 display is left alone. Asserts the kill matched — a no-op would make the test
    vacuous.
    """
    r = _exec(container, "pkill", "-9", "-f", "Xvfb.*1920x1080")
    assert r.returncode == 0, "no 1920x1080 Xvfb matched — the kill did nothing"


FLEX = "http://localhost:9377"


def _novnc_in_a_real_browser(container, url, settle=10):
    """Load the noVNC page in an actual browser and report what the operator would see.

    Uses the OTHER Camoufox instance (flex, :9377) — never the authenticated one under test —
    and talks to Caddy on container-localhost, so this is the real page, the real ingress, the
    real WebSocket. Everything else in this module is a hand-rolled client; only this one runs
    noVNC's own JavaScript, which is where the URL it connects to is actually decided.
    """
    body = json.dumps({"userId": "novnc-probe", "sessionKey": "default", "url": url})
    tab = _exec(container, "curl", "-s", "-m", "30", "-X", "POST", f"{FLEX}/tabs",
                "-H", "content-type: application/json", "-d", body)
    tab_id = json.loads(tab.stdout).get("tabId")
    assert tab_id, f"flex did not open the page: {tab.stdout}"
    try:
        time.sleep(settle)  # autoconnect + RFB handshake
        expr = ("(()=>{const s=document.querySelector('#noVNC_status');"
                "const p=new URLSearchParams(location.search).get('path');"
                "return {status:s&&s.textContent, cls:document.documentElement.className,"
                "ws:new URL(p, location.href).pathname};})()")
        ev = _exec(container, "curl", "-s", "-m", "30", "-X", "POST",
                   f"{FLEX}/tabs/{tab_id}/evaluate", "-H", "content-type: application/json",
                   "-d", json.dumps({"userId": "novnc-probe", "expression": expr}))
        return json.loads(ev.stdout).get("result") or {}
    finally:
        _exec(container, "curl", "-s", "-m", "20", "-X", "DELETE",
              f"{FLEX}/tabs/{tab_id}?userId=novnc-probe")


def _kill_arms(container):
    """Drop any detached opener processes so their arm threads can't touch a later test."""
    _exec(container, "pkill", "-f", "es.web_login")


def _watch_until_armed(base_url, timeout=90, sustain=5.0, interval=0.3):
    """Sample /web-login/ until it has been armed AND continuously connectable for `sustain`.

    Returns (violations, seconds_to_arm). A violation is the bug itself: the route served
    noVNC to a client that then could not complete the RFB handshake. Sampling never stops at
    the first success — a premature arm often works for one probe and dies a second later, so
    the connection must hold for `sustain` seconds of independent, first-try connects.
    """
    t0 = time.monotonic()
    violations, armed_at, good_since = [], None, None
    while time.monotonic() - t0 < timeout:
        state = _route_state(base_url)
        if state == "armed":
            armed_at = armed_at or round(time.monotonic() - t0, 2)
            ok, detail = _rfb_ok(base_url)
            if ok:
                good_since = good_since or time.monotonic()
                if time.monotonic() - good_since >= sustain:
                    return violations, armed_at
            else:
                good_since = None
                violations.append((round(time.monotonic() - t0, 2), detail))
        time.sleep(interval)
    raise AssertionError(
        f"/web-login/ never held a working noVNC connection within {timeout}s "
        f"(last state={_route_state(base_url)}, violations={violations})")


@pytest.fixture(autouse=True)
def _clean_window(everstone):
    c = everstone["container_name"]
    yield
    _kill_arms(c)
    _close_window(c)


# camofox tears an idle browser down in layers: a tab with no API calls for TAB_INACTIVITY_MS is
# reaped, a session untouched for SESSION_TIMEOUT_MS expires, and once no sessions remain the
# browser itself is shut down after BROWSER_IDLE_TIMEOUT_MS — taking Xvfb and x11vnc with it.
# Its defaults (5/10/5 min) make that a ~10-minute wait, so this container gets impatient ones.
# They stay well ABOVE es's 30s keep-alive beat, so the test still measures "does the heartbeat
# hold the window open", not "are the timeouts shorter than one beat".
IMPATIENT_ENV = {
    "SESSION_TIMEOUT_MS": "45000",
    "TAB_INACTIVITY_MS": "45000",
    "BROWSER_IDLE_TIMEOUT_MS": "5000",
}


@pytest.fixture(scope="module")
def impatient_everstone():
    """A second EverStone whose idle reapers fire in about a minute instead of ten."""
    name = "everstone-e2e-impatient"
    data_dir = tempfile.mkdtemp(prefix="everstone-e2e-impatient-")
    port = conftest._free_port()
    cfg_path = Path(data_dir) / "config.yaml"
    conftest._write_config(cfg_path, port)

    env_flags = [a for k, v in IMPATIENT_ENV.items() for a in ("-e", f"{k}={v}")]
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run([
        "docker", "run", "-d", "--name", name, "-p", f"{port}:80", *env_flags,
        "-v", f"{cfg_path}:/opt/config.yaml:ro", "-v", f"{data_dir}/data:/opt/data",
        conftest.IMAGE,
    ], check=True)

    base = f"http://localhost:{port}"
    for _ in range(60):
        try:
            if requests.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    else:
        subprocess.run(["docker", "logs", name])
        subprocess.run(["docker", "rm", "-f", name])
        pytest.fail("impatient container did not become healthy in time")

    yield {"base_url": base, "container_name": name}
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


# --- tests ---------------------------------------------------------------------------------

def test_novnc_is_only_revealed_once_the_rfb_path_works(everstone):
    """Cold start: from `open_signin` to a live desktop, the route must never expose a noVNC
    that a first-try client can't connect to."""
    c = everstone["container_name"]
    _open_signin(c)

    violations, armed_at = _watch_until_armed(everstone["base_url"])
    assert not violations, f"armed while the RFB path was down: {violations}"
    assert armed_at is not None


def test_route_shows_the_preparing_page_before_it_arms(everstone):
    """The pre-arm state is the self-refreshing page — not a dead noVNC, and not the Hermes UI.

    Guards the ordering inside open_signin: the route is swapped to "preparing" BEFORE the
    (multi-second, blocking) browser launch, so a link tapped immediately still lands somewhere
    friendly that advances on its own.
    """
    c = everstone["container_name"]
    _close_window(c)
    assert _route_state(everstone["base_url"]) == "closed"

    _open_signin(c)
    for _ in range(40):  # the swap is the first thing open_signin does
        if _route_state(everstone["base_url"]) == "preparing":
            break
        time.sleep(0.25)
    else:
        pytest.fail("route never showed the preparing page after open_signin")

    violations, _ = _watch_until_armed(everstone["base_url"])
    assert not violations, f"armed while the RFB path was down: {violations}"


def test_arm_waits_out_a_browser_restart(everstone):
    """The regression itself: a login opened while a DYING x11vnc is still listening.

    Reproduces the race deterministically — warm the browser so x11vnc is attached to a live
    display, then kill Camoufox and immediately open a login window. For ~2s (the watcher's
    poll) the old x11vnc still answers on :5900 while its display is gone, and the browser is
    relaunching onto a new one. Arming on that first pass is exactly what shipped a broken
    link; the arm must instead wait for the NEW display to be stably serving.
    """
    c = everstone["container_name"]
    _wait_browser(c)

    # Warm: a real display + attached x11vnc, then drop the route so the next open starts clean.
    _open_signin(c)
    _watch_until_armed(everstone["base_url"], sustain=2.0)
    _kill_arms(c)
    _close_window(c)

    # Kill the browser and open a login in the same breath — the stale-x11vnc window.
    _kill_the_display(c)
    _open_signin(c)

    violations, armed_at = _watch_until_armed(everstone["base_url"], timeout=120)
    assert not violations, (
        f"armed during the x11vnc restart — a client that clicked then would have seen "
        f"'Failed to connect to server': {violations}")
    assert armed_at is not None


def test_a_real_browser_gets_a_desktop(everstone):
    """What the operator actually does: open the link es sends and see the sign-in screen.

    Every other test here is a hand-rolled WS client pointed at a path WE chose, which cannot
    catch a bug in the URL noVNC builds for itself. noVNC resolves `path` with
    `new URL(path, location.href)` against /web-login/vnc.html, so a relative `path` silently
    becomes /web-login/web-login/websockify — it survives only because Caddy strips one prefix
    and websockify upgrades on any path, and it would break behind a stricter proxy. So this
    test asserts both halves: the URL noVNC resolves, and that it reaches a desktop.
    """
    c = everstone["container_name"]
    _open_signin(c)
    _watch_until_armed(everstone["base_url"])

    # The real query string es hands the operator, aimed at Caddy inside the container.
    login_url = _py(c, "import es.web_login as w; print(w.build_login_url('http://localhost'))")
    seen = _novnc_in_a_real_browser(c, login_url.stdout.strip())

    assert seen.get("ws") == "/web-login/websockify", f"noVNC resolved a wrong WS path: {seen}"
    assert "noVNC_connected" in (seen.get("cls") or ""), f"noVNC did not connect: {seen}"
    assert (seen.get("status") or "").startswith("Connected"), seen


def test_the_route_stops_serving_novnc_when_the_pipe_dies(everstone):
    """A window whose browser dies must not keep pointing at a dead websockify.

    This is the shape of the failure the operator reports: they open the link and get "Failed to
    connect to server" with nothing to act on. The route can't detect a death instantly, but it
    must notice within seconds and go back to the self-refreshing preparing page (and on to the
    "ask the assistant" page if the browser never returns), instead of serving a dead noVNC for
    the rest of the window.
    """
    c, base = everstone["container_name"], everstone["base_url"]
    _open_signin(c)
    _watch_until_armed(base)

    _kill_the_display(c)
    killed_at, violations, gave_up_at = time.monotonic(), [], None
    while time.monotonic() - killed_at < 120:
        state = _route_state(base)
        if state != "armed":
            gave_up_at = round(time.monotonic() - killed_at, 1)
            break
        ok, detail = _rfb_ok(base)
        if not ok:
            violations.append(round(time.monotonic() - killed_at, 1))
        time.sleep(1)

    assert gave_up_at is not None, "route kept serving noVNC after the browser died"
    assert gave_up_at <= 45, f"took {gave_up_at}s to stop serving a dead noVNC"
    # A bounded blind spot is expected (nothing can notice instantly); an unbounded one is the bug.
    assert not violations or violations[-1] <= gave_up_at, violations
    assert _route_state(base) in ("preparing", "closed")


def test_an_open_login_window_outlives_the_idle_reapers(impatient_everstone):
    """An open login window must survive a human's pace — the failure that reaches the operator.

    Signing in by hand is minutes of typing that camofox cannot see: VNC input never touches its
    API, so to its reapers the login tab looks abandoned from the moment it opens. Left alone,
    the tab is reaped, the empty session closes, the browser idle-shuts-down, Xvfb dies and
    x11vnc exits — while `/web-login/` is still armed. The operator then opens the link they
    were sent and websockify answers the WebSocket with "Failed to connect": the exact report
    that started this. Nothing here involves the startup race; the window simply outlived its
    browser.

    Runs ~4 minutes: camofox's reapers tick every 60s (not configurable), so proving survival
    means outlasting several ticks — the window this container dies in without the keep-alive.
    """
    c, base = impatient_everstone["container_name"], impatient_everstone["base_url"]
    # No _wait_browser here: with a 5s idle timeout this container shuts the pre-warmed browser
    # down again immediately. open_signin launches it on demand, which is the real path anyway.
    _open_signin(c)
    _watch_until_armed(base, timeout=120)

    deadline = time.monotonic() + 200
    while time.monotonic() < deadline:
        elapsed = round(time.monotonic() - (deadline - 200))
        ok, detail = _rfb_ok(base)
        assert ok, f"login window died after ~{elapsed}s of hand-login time: {detail}"
        assert _route_state(base) == "armed"
        time.sleep(5)

    # The heartbeat is what did it: the session (and therefore the browser) is still there.
    health = _auth_health(c)
    assert health.get("activeSessions", 0) >= 1, f"session was reaped: {health}"
    assert health.get("browserConnected") is True, health
    _kill_arms(c)
    _close_window(c)


def test_login_url_tells_novnc_to_reconnect(everstone):
    """The URL the operator receives must survive a mid-login x11vnc restart.

    Server-side stability decides the FIRST connect; `reconnect` covers the drop AFTER it —
    the watcher restarts x11vnc on any display change, which would otherwise end the session
    with "Something went wrong, connection is closed" while the operator is mid-sign-in.
    """
    c = everstone["container_name"]
    url = _py(c, "import es.web_login as w; print(w.build_login_url('https://es.example.com'))").stdout.strip()
    assert url.startswith("https://es.example.com/web-login/vnc.html?")
    for param in ("path=/web-login/websockify", "autoconnect=true", "reconnect=true",
                  "reconnect_delay=2000"):
        assert param in url, f"{param} missing from {url}"
