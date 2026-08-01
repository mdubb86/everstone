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
