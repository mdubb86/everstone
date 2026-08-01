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


def run_es_login(profile, *, fetch_state, probe_home, open_signin, close_window, login_url):
    """Idempotent login-window orchestration (agent never touches the browser — this is the
    logic behind the es_login tool):

      fetch_state(profile) -> stored storage_state (for the cheap cookie pre-gate)
      probe_home(profile)  -> live {acct, signin} from a google.com browse (only if cookies pass)
      open_signin(profile) -> add the Caddy route + park the browser on the sign-in page
      close_window()       -> remove the Caddy route

    Signed in iff the anchor cookies are present AND the live probe confirms it; anything else
    opens the window and returns the noVNC login link for the operator to complete by hand.
    """
    signed_in = has_session_cookies(fetch_state(profile)) and signed_in_from_home(probe_home(profile))
    if signed_in:
        close_window()
        return {"status": "logged_in", "profile": profile}
    open_signin(profile)
    return {"status": "awaiting_login", "profile": profile, "login_url": login_url}


# --- Real I/O deps (thin; integration-tested in-container, not unit-tested) --------------
import os
import time
import threading
import subprocess
import httpx

_CAMOFOX = os.environ.get("CAMOFOX_URL", "http://localhost:9377")
_CADDYFILE = "/opt/config/caddy/Caddyfile"
_HOME_URL = "https://www.google.com"
_SIGNIN_URL = "https://accounts.google.com"  # all current profiles are Google; per-profile later
# Read the top-right profile affordance: "Google Account" avatar (signed in) vs ServiceLogin link.
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
    # Navigate to google.com, then poll the evaluate until the auth affordance actually renders
    # (google.com finishes loading a beat after navigation returns). Definitive as soon as either
    # the avatar or the sign-in link is present; gives up after ~4s with the last read.
    tab = _navigate(profile, _HOME_URL)
    tid = tab.get("tabId")
    sig = {}
    for _ in range(16):  # up to ~8s — google.com's header can render a beat after nav returns
        sig = _evaluate(profile, tid, _PROBE_JS)
        if sig.get("acct") or sig.get("signin"):
            break
        time.sleep(0.5)
    return sig


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
