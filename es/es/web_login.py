"""Authenticated-browser-session helpers (Layer 1: persistent web login).

Logic behind the `es_login` MCP tool and consumer logout-detection. The agent never
touches the browser directly — it calls es tools, which drive camofox-browser and hand
the operator a noVNC link to complete the real login by hand.
"""

# Liveness is decided by a LIVE probe to google.com, never by inspecting stored cookies
# (which can be stale while the session is dead server-side). google.com is the most natural
# page to poll — warm-keeping traffic there doesn't look like an auth check. The top-right
# profile affordance is the tell: a "Google Account" avatar ([aria-label*="Account"]) when
# signed in, a ServiceLogin "Sign in" link (a[href*="ServiceLogin"]) when signed out. The
# browser evaluates those two selectors and reports {acct, signin}; we interpret them here.


def signed_in_from_home(signal):
    """True iff google.com shows the account avatar and no sign-in link. Ambiguous => False."""
    s = signal or {}
    return bool(s.get("acct")) and not bool(s.get("signin"))


import re

# The noVNC seeding surface + its Caddy route. The /web-login/ path is ALWAYS present in the
# Caddyfile: its base state is the static "not active" page (config/caddy/Caddyfile), and
# es_login SWAPS the block's body rather than adding/removing it. So an idle or expired link
# never falls through to the catch-all (the Hermes UI password screen) — it shows a clear
# "ask the assistant to start the login again" page. websockify serves noVNC on
# container-localhost:6080 (never published); Caddy is the only ingress, so the block's MODE
# — reverse-proxy vs static page — is the access gate.
_ARMED_BLOCK = "\thandle_path /web-login/* {\n\t\treverse_proxy 127.0.0.1:6080\n\t}\n"
# The auth target is generic (google today, others later), so the copy says "login", not
# "Google login". Keep this page in sync with the /web-login/ block in config/caddy/Caddyfile
# (the base copy) — the swap matches the block by its handle_path matcher, so drift still
# works; an identical copy just keeps the base page and the post-close page the same.
_CLOSED_PAGE = ("<!doctype html><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Login window closed</title>"
                "<body style='font-family:system-ui,sans-serif;max-width:34rem;margin:14vh auto;"
                "padding:0 1.5rem;text-align:center;color:#1c1c1e'>"
                "<h1 style='font-size:1.35rem;margin-bottom:.4rem'>Login window isn't active</h1>"
                "<p style='color:#555;line-height:1.55'>There's no sign-in in progress right now. "
                "Ask your assistant to start the login again, then open the fresh link it sends "
                "you.</p></body>")
_CLOSED_BLOCK = ("\thandle_path /web-login/* {\n"
                 "\t\theader Content-Type \"text/html; charset=utf-8\"\n"
                 "\t\trespond `" + _CLOSED_PAGE + "` 200\n"
                 "\t}\n")
_CATCH_ALL = "\thandle /* {"
# Match the whole /web-login/ handle block (any body) so a swap is robust against formatting
# drift between here and the Caddyfile template. Non-greedy to the first line that closes it.
_ROUTE_RE = re.compile(r"\thandle_path /web-login/\* \{.*?\n\t\}\n", re.DOTALL)


def build_login_url(public_url):
    """The noVNC seeding URL under the public base — WS path prefixed so it connects under
    the /web-login/ mount, and auto-connecting so the operator just taps and logs in.

    `path` must be ABSOLUTE. noVNC resolves it with `new URL(path, location.href)` against a page
    at /web-login/vnc.html, so the relative form (`web-login/websockify`) becomes
    /web-login/web-login/websockify. That still happens to work here — Caddy's handle_path strips
    the first segment and websockify upgrades a WebSocket on any path — but it only works by
    luck, and any proxy in front that is stricter about paths breaks the login for good.

    `reconnect` covers the drop that happens MID-LOGIN: the vnc plugin's watcher restarts
    x11vnc whenever Camoufox's display changes (browser restart), which kills a live session.
    Without it noVNC shows "Something went wrong, connection is closed" and stops. Note it does
    NOT rescue a failed FIRST connect — noVNC starts with `inhibitReconnect = true` and only
    clears it in connectFinished (app/ui.js), so a never-connected client never retries. That
    hole is closed on the server side instead: _run_window only reveals noVNC while the path is
    STABLY serving, so the first connect isn't made into a race.
    """
    return (public_url.rstrip("/") + "/web-login/vnc.html?path=/web-login/websockify"
            "&autoconnect=true&reconnect=true&reconnect_delay=2000")


def _set_route_block(caddyfile_text, block):
    """Ensure the /web-login/ handle block is exactly `block`: swap it in place if present,
    else insert it just before the catch-all so the specific path wins. Idempotent. A function
    replacement (not a string) keeps backslashes in `block` literal."""
    if _ROUTE_RE.search(caddyfile_text):
        return _ROUTE_RE.sub(lambda _m: block, caddyfile_text, count=1)
    return caddyfile_text.replace(_CATCH_ALL, block + "\n" + _CATCH_ALL, 1)


def add_route_block(caddyfile_text):
    """Arm: point /web-login/ at the noVNC backend (websockify :6080) for an active login."""
    return _set_route_block(caddyfile_text, _ARMED_BLOCK)


def close_route_block(caddyfile_text):
    """Close: swap /web-login/ back to the static "login not active" page. Never removes the
    block, so an expired/idle link shows the friendly page instead of the Hermes UI."""
    return _set_route_block(caddyfile_text, _CLOSED_BLOCK)


# The "preparing" page: shown the instant es_login is called, before the browser + display +
# x11vnc have spun up (they start on demand, a few seconds). It META-REFRESHES itself — NOT
# JS/CSS — because Caddy's `respond` body treats `{...}` as placeholders, so braces would be
# mangled; meta-refresh needs none. Each reload re-serves this page while preparing; the moment
# the background arm swaps the route to the live noVNC, the next reload lands on vnc.html (query
# params intact) and autoconnects. So an early click gets a friendly self-advancing page instead
# of noVNC's "failed to connect".
_PREPPING_PAGE = ("<!doctype html><meta charset=utf-8>"
                  "<meta name=viewport content='width=device-width,initial-scale=1'>"
                  "<meta http-equiv=refresh content=2>"
                  "<title>Preparing login</title>"
                  "<body style='font-family:system-ui,sans-serif;max-width:34rem;margin:14vh auto;"
                  "padding:0 1.5rem;text-align:center;color:#1c1c1e'>"
                  "<h1 style='font-size:1.35rem;margin-bottom:.4rem'>Preparing your login…</h1>"
                  "<p style='color:#555;line-height:1.55'>Starting the secure browser. This page "
                  "refreshes on its own and will open the sign-in screen in a few seconds.</p></body>")
_PREPPING_BLOCK = ("\thandle_path /web-login/* {\n"
                   "\t\theader Content-Type \"text/html; charset=utf-8\"\n"
                   "\t\trespond `" + _PREPPING_PAGE + "` 200\n"
                   "\t}\n")


def prepping_route_block(caddyfile_text):
    """Preparing: swap /web-login/ to the static, self-refreshing "preparing login" page shown
    while the browser + VNC server spin up. The background arm flips it to the live noVNC."""
    return _set_route_block(caddyfile_text, _PREPPING_BLOCK)


# The durable Google session-anchor cookies. Their PRESENCE is a cheap NECESSARY condition
# for being signed in (not sufficient — Google may have invalidated them server-side). Absent
# => definitely signed out: skip the live probe and don't warm-keep. Present => confirm live.
_SESSION_ANCHORS = {"__Secure-1PSID", "__Secure-3PSID"}


def has_session_cookies(storage_state):
    """Cheap pre-gate: are the durable session-anchor cookies present in the stored state?"""
    cookies = (storage_state or {}).get("cookies") or []
    return any(c.get("name") in _SESSION_ANCHORS for c in cookies)


def run_es_login(profile, *, probe_home, capture, open_signin, close_window, login_url):
    """Idempotent login-window orchestration (the logic behind es_login; the agent never touches
    the browser). The live google.com probe is authoritative AND self-healing: it creates/restores
    the profile's session (the persistence plugin re-injects the durable login on session create)
    and reads the result.

      probe_home(profile)  -> live {acct, signin}
      capture(profile)     -> persist the confirmed session (fires the storage_state checkpoint)
      open_signin(profile) -> add the Caddy route + park the browser on the sign-in page
      close_window()       -> remove the Caddy route

    Signed in -> capture + close. Signed out -> open the window, return the noVNC login link.

    No cheap cookie pre-gate here: reading stored cookies is unreliable before a session exists
    (404 right after a restart; stale right after a fresh login), and the probe must run anyway to
    restore + confirm. The cookie pre-check belongs to the warm-keeper, which reads the durable file.
    """
    if signed_in_from_home(probe_home(profile)):
        capture(profile)
        close_window()
        return {"status": "logged_in", "profile": profile}
    open_signin(profile)
    return {"status": "awaiting_login", "profile": profile, "login_url": login_url}


# --- Real I/O deps (thin; integration-tested in-container, not unit-tested) --------------
import base64
import os
import socket
import threading
import time
import subprocess
import httpx

# camofox-auth (:9378), NOT camofox-flex (:9377). es owns the AUTHENTICATED browser
# instance; flex is login-less and its profiles are wiped at every start, so pointing
# es here at CAMOFOX_URL would silently break es_login and the warm-keeper.
_CAMOFOX = os.environ.get("CAMOFOX_AUTH_URL", "http://localhost:9378")
_CADDYFILE = "/opt/config/caddy/Caddyfile"
_HOME_URL = "https://www.google.com"
_SIGNIN_URL = "https://accounts.google.com"  # all current profiles are Google; per-profile later
# The top-right profile affordance: "Google Account" avatar (signed in) vs ServiceLogin link.
_AFFORDANCE_SELECTOR = '[aria-label*="Account"], a[href*="ServiceLogin"]'
_PROBE_JS = ('(()=>{const signin=!!document.querySelector(\'a[href*="ServiceLogin"]\');'
             'const acct=!!document.querySelector(\'[aria-label*="Account"]\');'
             'return {acct, signin};})()')


def _navigate(profile, url, timeout=30):
    r = httpx.post(f"{_CAMOFOX}/tabs", json={"userId": profile, "sessionKey": "default", "url": url},
                   timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_state(profile):
    # GET storage_state also fires the persistence checkpoint, so a signed-in probe re-persists.
    r = httpx.get(f"{_CAMOFOX}/sessions/{profile}/storage_state", timeout=15)
    return r.json() if r.status_code == 200 else {}


def _evaluate(profile, tab_id, expression):
    r = httpx.post(f"{_CAMOFOX}/tabs/{tab_id}/evaluate",
                   json={"userId": profile, "expression": expression}, timeout=20)
    r.raise_for_status()
    return (r.json() or {}).get("result") or {}


def _close_tab(profile, tab_id):
    """Close a tab. Best-effort: cleanup must never mask the caller's result.

    camofox's POST /tabs ALWAYS creates a new tab (sessionKey only groups them,
    it does not dedupe), so every navigate leaks one unless the caller closes it.
    Nothing here did, and the leak is invisible: GET /tabs returns [] unless you
    pass ?userId=<profile>. Measured effect — camofox RSS grew 938MB -> 2013MB
    with 8 abandoned tabs holding live google.com pages, taking the VM to 159MB
    free and swapping.
    """
    if not tab_id:
        return
    try:
        httpx.delete(f"{_CAMOFOX}/tabs/{tab_id}", params={"userId": profile}, timeout=20)
    except Exception:  # noqa: BLE001
        pass


def probe_home(profile):
    # Navigate to google.com, then WAIT for the definitive top-right affordance to render (avatar
    # OR sign-in link) before reading it — robust against the header rendering a beat after nav
    # returns. If neither ever appears (e.g. an interstitial), the wait times out and we evaluate
    # anyway: {acct:false, signin:false} => treated as signed-out (prompt a login, don't assume).
    tab = _navigate(profile, _HOME_URL)
    tid = tab.get("tabId")
    try:
        try:
            httpx.post(f"{_CAMOFOX}/tabs/{tid}/wait",
                       json={"userId": profile, "selector": _AFFORDANCE_SELECTOR, "timeout": 10000},
                       timeout=15)
        except Exception:  # noqa: BLE001 — selector-not-found is fine; the evaluate handles the fallback
            pass
        return _evaluate(profile, tid, _PROBE_JS)
    finally:
        # run_warm_keep calls this every 6h and run_es_login on every retry, so
        # an unclosed tab here leaked four live google.com pages a day, forever.
        _close_tab(profile, tid)


def _reload_caddy(text):
    with open(_CADDYFILE, "w") as f:
        f.write(text)
    subprocess.run(["caddy", "reload", "--config", _CADDYFILE, "--adapter", "caddyfile"],
                   check=True, capture_output=True)


def _swap_route(to_block_fn):
    """Read the Caddyfile, apply a route-block transform, and reload. Serialized so the
    background arm and a concurrent close can't interleave a read/write."""
    with _route_lock:
        with open(_CADDYFILE) as f:
            _reload_caddy(to_block_fn(f.read()))


# websockify (noVNC) bridges browser WebSockets to x11vnc (:5900) once the browser's display is
# up — the vnc plugin starts both on demand. We probe the WEBSOCKIFY hop, not x11vnc directly:
# that's the exact path the operator's browser takes through Caddy.
_NOVNC_HOST, _NOVNC_PORT = "127.0.0.1", 6080
_route_lock = threading.Lock()
# Generation guard: each open_signin/close bumps this so a stale background arm can't flip the
# route back open after a newer call has moved on.
_arm_generation = 0


def _next_generation():
    """Claim the window: supersedes every background worker started by an earlier call."""
    global _arm_generation
    _arm_generation += 1
    return _arm_generation


def _rfb_over_websockify(timeout=2.0):
    """True once the WHOLE noVNC path serves an RFB banner: a WebSocket upgrade to websockify
    (:6080) that x11vnc (:5900) answers with `RFB `. This is deliberately end-to-end — the same
    hop chain the operator's browser makes through Caddy — because a raw :5900 accept can pass
    while websockify is still refusing/wedged, and the client only cares about the whole chain.

    Hand-rolled (~20 lines) rather than pulling in a websocket dependency for one probe: send the
    RFC 6455 handshake, expect 101, then read the first server frame's payload (unmasked, and
    small — the banner is 12 bytes, so no continuation handling is needed)."""
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET /websockify HTTP/1.1\r\nHost: {_NOVNC_HOST}:{_NOVNC_PORT}\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
           "Sec-WebSocket-Protocol: binary\r\n\r\n")
    try:
        with socket.create_connection((_NOVNC_HOST, _NOVNC_PORT), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(req.encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    return False
                buf += chunk
            head, rest = buf.split(b"\r\n\r\n", 1)
            if b" 101 " not in head.split(b"\r\n", 1)[0]:
                return False
            # One frame: [FIN|opcode][len][payload]. len < 126 always here (banner is 12 bytes).
            while len(rest) < 2:
                chunk = s.recv(4096)
                if not chunk:
                    return False
                rest += chunk
            payload = rest[2:]
            while len(payload) < 4:
                chunk = s.recv(4096)
                if not chunk:
                    return False
                payload += chunk
            return payload.startswith(b"RFB ")
    except (OSError, ValueError):
        return False


def _vnc_display():
    """The display x11vnc is currently attached to, per camofox's vnc plugin — or None if the
    watcher has no live x11vnc. Used as an identity: if this CHANGES between probes, the browser
    restarted onto a new display and x11vnc was restarted with it."""
    try:
        r = httpx.get(f"{_CAMOFOX}/vnc/status", timeout=2)
        j = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return None
    return j.get("display") if j.get("running") else None


def _vnc_ready():
    """One readiness SAMPLE: (path serves RFB, display it's serving). Both parts matter — see
    _run_window for why a single positive sample is not enough to arm on."""
    return _rfb_over_websockify(), _vnc_display()


# camofox reaps a tab that has seen no API activity for TAB_INACTIVITY_MS (5 min by default),
# then closes the now-empty session, which lets the browser idle-shut-down — taking Xvfb and
# x11vnc with it. A hand login is minutes of typing that camofox cannot see (VNC input never
# reaches its API), so an un-beaten login window loses its sign-in page mid-login and then its
# whole browser. GET /tabs/<id>/downloads is the cheapest call that bumps BOTH counters the
# reapers read (tabState.toolCalls and session.lastAccess) and it touches nothing on the page.
# 30s, not 60: camofox's reapers run on a 60s tick, so a beat every 30s guarantees every tick
# sees activity even if one beat is lost to a hiccup.
_KEEPALIVE_INTERVAL = 30.0


def _beat(profile, tab_id):
    """One keep-alive beat. Best-effort: a missed beat costs nothing (the reapers need a full
    quiet tick), and the supervisor notices if the window dies anyway."""
    try:
        httpx.get(f"{_CAMOFOX}/tabs/{tab_id}/downloads", params={"userId": profile}, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _swap_if_current(gen, to_block_fn):
    if _arm_generation == gen:
        _swap_route(to_block_fn)


def _launch_signin_tab(profile, retry_for=45.0, timeout=90.0, backoff=5.0):
    """Open the sign-in page, tolerating a browser that is cold or not up yet. Returns the tab id,
    or None if it never came up.

    Both failures are real and neither is exotic. camofox-auth binds its port well after the
    container reports healthy, so a login triggered in that window used to die on connect; and a
    first Camoufox launch on a loaded box can outlast a short HTTP timeout. Either way the old
    code raised out of open_signin AFTER the route had been swapped to "preparing" — so the
    operator was left refreshing a page that would never advance, forever. Retrying here (and
    doing it in the background, off the tool's critical path) turns a slow start into a slightly
    longer "Preparing your login…" instead of a dead end. `retry_for` bounds the RETRIES, not the
    attempts: a service that takes half a minute to bind shouldn't exhaust three quick tries."""
    give_up_at = time.monotonic() + retry_for
    while True:
        try:
            return (_navigate(profile, _SIGNIN_URL, timeout=timeout) or {}).get("tabId")
        except Exception:  # noqa: BLE001 — connection refused, launch timeout, 5xx: all retryable
            if time.monotonic() >= give_up_at:
                return None
            time.sleep(backoff)


def _run_window(gen, profile=None, interval=0.5, stable_for=3.0,
                arm_timeout=30.0, watch_every=5.0, grace=3, lifetime=900.0):
    """Own /web-login/ for the lifetime of one login window, keeping the route HONEST: it shows
    the live noVNC only while the noVNC path actually works, and the self-refreshing "preparing"
    page whenever it doesn't. Generation-guarded, so a newer open_signin or a close supersedes it.

    Arming waits for `stable_for` seconds of CONTINUOUS readiness on the SAME display, not the
    first passing probe. The vnc plugin's watcher (plugins/vnc/vnc-watcher.sh) restarts x11vnc
    whenever Camoufox's display changes, and x11vnc exits outright when its X server goes away, so
    :5900 can serve, drop and come back during startup. Arming inside that gap published a link
    that could not connect — and noVNC's autoconnect never retries a first connect (see
    build_login_url), so the operator was stuck with "Failed to connect to server."

    Watching matters just as much, because the pipe can die at any point AFTER arming — the
    browser can be idle-reaped, OOM-killed or restarted onto a new display — and a one-shot arm
    left the route pointing at a dead websockify for the rest of the window. `grace` consecutive
    failures put the preparing page back, which self-refreshes and re-arms if the path returns,
    or falls through to the "not active" page if it doesn't come back within `arm_timeout`. The
    operator always sees the truth, and something they can act on.
    """
    # Samples are `interval` apart, so N samples span (N-1)*interval seconds of continuous uptime.
    needed = int(stable_for / interval) + 1

    def worker():
        # Launch INSIDE the supervisor: the browser can take a while to come up, and none of that
        # belongs on es_login's critical path — the operator already has the link and is looking
        # at the self-refreshing preparing page. The arm clock only starts once we're launched.
        tab_id = _launch_signin_tab(profile) if profile else None
        if profile and not tab_id:
            _swap_if_current(gen, close_route_block)  # browser never came up — say so, don't spin
            return
        end = time.monotonic() + lifetime
        armed, streak, on_display, misses = False, 0, None, 0
        arm_deadline = time.monotonic() + arm_timeout
        next_watch = next_beat = time.monotonic() + _KEEPALIVE_INTERVAL
        while time.monotonic() < end:
            if _arm_generation != gen:
                return  # superseded by a newer open_signin/close
            now = time.monotonic()
            if not armed:
                ok, display = _vnc_ready()
                if ok and display and display == on_display:
                    streak += 1
                elif ok and display:
                    on_display, streak = display, 1  # first pass, or the display just changed
                else:
                    on_display, streak = None, 0
                if streak >= needed:
                    _swap_if_current(gen, add_route_block)
                    armed, misses, next_watch = True, 0, now + watch_every
                elif now >= arm_deadline:
                    # Never came up (or never came back) — stop the preparing page refreshing
                    # forever and tell the operator to ask for a fresh link.
                    _swap_if_current(gen, close_route_block)
                    return
            elif now >= next_watch:
                next_watch = now + watch_every
                misses = 0 if _rfb_over_websockify() else misses + 1
                if misses >= grace:
                    _swap_if_current(gen, prepping_route_block)
                    armed, streak, on_display = False, 0, None
                    arm_deadline = now + arm_timeout
            if tab_id and now >= next_beat:
                next_beat = now + _KEEPALIVE_INTERVAL
                _beat(profile, tab_id)
            time.sleep(interval)

    threading.Thread(target=worker, daemon=True).start()


def open_signin(profile):
    """Open a login window: show the preparing page NOW, and hand everything else to a background
    supervisor so es_login answers immediately with a link the operator can already open."""
    # The preparing page first, so a link tapped a second later gets a friendly self-advancing
    # page rather than a dead noVNC or the Hermes UI.
    _swap_route(prepping_route_block)
    # Claim the window before anything slow, so a close landing mid-launch supersedes us.
    gen = _next_generation()
    # The supervisor launches the browser (retrying a cold start), arms the route once the noVNC
    # path is stably serving, keeps the tab alive while the operator types, and takes the route
    # back down if the path dies.
    _run_window(gen, profile)


def close_window():
    global _arm_generation
    _arm_generation += 1  # invalidate any pending arm so it can't re-open after we close
    _swap_route(close_route_block)


# No polling: login completion is handled by the human ("done" → agent re-calls es_login, which
# is idempotent — probes, captures, closes). The ONLY background need is a fail-safe timeout that
# removes the /web-login route if the operator never returns. One shared route → one global timer.
_close_timer = None


def cancel_window_close():
    """Cancel a pending timeout-close (called when es_login confirms logged_in and closes now)."""
    global _close_timer
    if _close_timer is not None:
        _close_timer.cancel()
        _close_timer = None


def schedule_window_close(delay_s=600):
    """Arm a one-shot timer to remove the /web-login route after delay_s (default 10 min) — the
    fail-safe if the operator never completes login. Re-arming cancels any prior timer so a stale
    one can't close a freshly re-opened window. close_window() is idempotent, so firing after an
    already-closed window is a harmless no-op."""
    global _close_timer
    cancel_window_close()
    _close_timer = threading.Timer(delay_s, close_window)
    _close_timer.daemon = True
    _close_timer.start()


import hashlib
import json

_PROFILE_DIR = os.environ.get("CAMOFOX_PROFILE_DIR", "/opt/data/browser/profiles")


def read_durable_state(profile):
    """Read a profile's PERSISTED storage_state file (last-known session) WITHOUT needing an
    active session — camofox-browser keys it by sha256(userId)[:32]. For the warm-keeper's cheap
    'is there anything worth keeping alive?' pre-gate. Returns {} if absent/unreadable."""
    h = hashlib.sha256(str(profile).encode()).hexdigest()[:32]
    try:
        with open(os.path.join(_PROFILE_DIR, h, "storage-state.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run_warm_keep(profile, *, read_durable, probe_home, persist):
    """Keep a durable session warm. Cheap durable-cookie pre-gate: no stored anchors => nothing to
    keep alive, skip the browse. Otherwise browse google.com (rotates the short-lived *PSIDTS
    cookies that go stale when idle) and re-persist. Reports whether the session is still live, but
    NEVER opens a login window — only a real tool use may trigger a re-login (a dead profile is left
    dead until then)."""
    if not has_session_cookies(read_durable(profile)):
        return {"profile": profile, "warmed": False, "reason": "no_session"}
    signed_in = signed_in_from_home(probe_home(profile))
    persist(profile)
    return {"profile": profile, "warmed": True, "signed_in": signed_in}
