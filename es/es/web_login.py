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


# The noVNC seeding surface + its Caddy route. The route exists ONLY during an active login
# window and is removed after (never in the base config) — see the persistent-web-login spec.
# websockify serves noVNC on container-localhost:6080 (never published); Caddy is the only
# ingress, so the route's presence/absence is the access gate.
_ROUTE_BLOCK = "\thandle_path /web-login/* {\n\t\treverse_proxy 127.0.0.1:6080\n\t}\n\n"
_CATCH_ALL = "\thandle /* {"


def build_login_url(public_url):
    """The noVNC seeding URL under the public base — WS path prefixed so it connects under
    the /web-login/ mount, and auto-connecting so the operator just taps and logs in."""
    return public_url.rstrip("/") + "/web-login/vnc.html?path=web-login/websockify&autoconnect=true"


def add_route_block(caddyfile_text):
    """Insert the /web-login route just before the catch-all so the specific path wins. Idempotent."""
    if "/web-login/" in caddyfile_text:
        return caddyfile_text
    return caddyfile_text.replace(_CATCH_ALL, _ROUTE_BLOCK + _CATCH_ALL, 1)


def remove_route_block(caddyfile_text):
    """Remove the /web-login route block. Idempotent (no-op when absent)."""
    return caddyfile_text.replace(_ROUTE_BLOCK, "", 1)


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

_CAMOFOX = os.environ.get("CAMOFOX_URL", "http://localhost:9377")
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


def probe_home(profile):
    # Navigate to google.com, then WAIT for the definitive top-right affordance to render (avatar
    # OR sign-in link) before reading it — robust against the header rendering a beat after nav
    # returns. If neither ever appears (e.g. an interstitial), the wait times out and we evaluate
    # anyway: {acct:false, signin:false} => treated as signed-out (prompt a login, don't assume).
    tab = _navigate(profile, _HOME_URL)
    tid = tab.get("tabId")
    try:
        httpx.post(f"{_CAMOFOX}/tabs/{tid}/wait",
                   json={"userId": profile, "selector": _AFFORDANCE_SELECTOR, "timeout": 10000},
                   timeout=15)
    except Exception:  # noqa: BLE001 — selector-not-found is fine; the evaluate handles the fallback
        pass
    return _evaluate(profile, tid, _PROBE_JS)


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
        _reload_caddy(remove_route_block(f.read()))


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
