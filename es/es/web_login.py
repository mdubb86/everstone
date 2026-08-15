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
    the /web-login/ mount, and auto-connecting so the operator just taps and logs in."""
    return public_url.rstrip("/") + "/web-login/vnc.html?path=web-login/websockify&autoconnect=true"


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
import os
import threading
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


def _navigate(profile, url):
    r = httpx.post(f"{_CAMOFOX}/tabs", json={"userId": profile, "sessionKey": "default", "url": url}, timeout=30)
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


def open_signin(profile):
    with open(_CADDYFILE) as f:
        _reload_caddy(add_route_block(f.read()))
    _navigate(profile, _SIGNIN_URL)  # park the browser on the sign-in page for the noVNC login


def close_window():
    with open(_CADDYFILE) as f:
        _reload_caddy(close_route_block(f.read()))


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
