import time

import es.web_login as wl
from es.web_login import signed_in_from_home

# Liveness is decided by a LIVE probe to google.com — the most natural page to poll, so
# warm-keeping traffic doesn't look like an auth check. Stored cookies can be stale, so we
# never trust them; instead we read the top-right profile affordance, which flips between the
# "Google Account" avatar (signed in) and a ServiceLogin "Sign in" link (signed out). The
# browser reports two booleans; this interprets them. Ambiguous/blank => treat as NOT signed
# in (better to prompt a login than act on a dead session).


def test_signed_in_when_account_avatar_present_and_no_signin_link():
    assert signed_in_from_home({"acct": True, "signin": False}) is True


def test_not_signed_in_when_signin_link_present():
    assert signed_in_from_home({"acct": False, "signin": True}) is False


def test_ambiguous_both_present_is_not_signed_in():
    assert signed_in_from_home({"acct": True, "signin": True}) is False


def test_blank_page_neither_present_is_not_signed_in():
    assert signed_in_from_home({"acct": False, "signin": False}) is False


def test_missing_signal_is_not_signed_in():
    assert signed_in_from_home({}) is False
    assert signed_in_from_home(None) is False


from es.web_login import build_login_url, add_route_block, close_route_block, prepping_route_block

CADDY = "\thandle /oauth {\n\t\treverse_proxy localhost:8081\n\t}\n\n\thandle /* {\n\t\tredir * /hermes/ 302\n\t}\n"


def test_build_login_url_appends_novnc_path_and_autoconnect():
    assert build_login_url("https://everstone.tail.ts.net") == \
        ("https://everstone.tail.ts.net/web-login/vnc.html?path=/web-login/websockify"
         "&autoconnect=true&reconnect=true&reconnect_delay=2000")


def test_build_login_url_asks_novnc_to_reconnect():
    # The vnc watcher restarts x11vnc on any display change, which drops a LIVE session
    # mid-login. Without reconnect, noVNC gives up on the first drop and the operator has to
    # ask for a whole new link. (It does not rescue a failed FIRST connect — noVNC never
    # retries one — which is why the arm waits for a stable path instead.)
    url = build_login_url("https://everstone.tail.ts.net")
    assert "reconnect=true" in url and "reconnect_delay=2000" in url


def test_build_login_url_strips_trailing_slash():
    assert build_login_url("http://everstone.test:8080/").startswith(
        "http://everstone.test:8080/web-login/vnc.html")


def test_add_route_block_inserts_before_catch_all():
    out = add_route_block(CADDY)
    assert "handle_path /web-login/*" in out
    # inserted BEFORE the catch-all so the specific path wins
    assert out.index("/web-login/") < out.index("handle /*")


def test_add_route_block_is_idempotent():
    once = add_route_block(CADDY)
    assert add_route_block(once) == once


def test_close_route_block_swaps_armed_to_static_page():
    armed = add_route_block(CADDY)
    closed = close_route_block(armed)
    # The path stays OWNED (never falls through to the catch-all / Hermes UI) ...
    assert "handle_path /web-login/*" in closed
    assert closed.index("/web-login/") < closed.index("handle /*")
    # ... but no longer proxies to the noVNC backend ...
    assert "reverse_proxy 127.0.0.1:6080" not in closed
    # ... it serves the static "not active" page instead.
    assert "Login window isn't active" in closed


def test_close_route_block_inserts_static_when_absent():
    out = close_route_block(CADDY)
    assert "handle_path /web-login/*" in out
    assert "Login window isn't active" in out
    assert out.index("/web-login/") < out.index("handle /*")


def test_close_route_block_is_idempotent():
    once = close_route_block(CADDY)
    assert close_route_block(once) == once


def test_add_route_block_swaps_static_to_armed():
    # The reported bug: base config has the static page; arming must switch it to the proxy
    # (an idle-then-armed link previously stayed the static/Hermes page).
    armed = add_route_block(close_route_block(CADDY))
    assert "reverse_proxy 127.0.0.1:6080" in armed
    assert "Login window isn't active" not in armed
    assert armed.index("/web-login/") < armed.index("handle /*")


def test_prepping_route_block_swaps_to_self_refreshing_page():
    out = prepping_route_block(CADDY)
    assert "handle_path /web-login/*" in out
    assert "http-equiv=refresh" in out              # advances client-side on its own
    assert "Preparing your login" in out
    assert "reverse_proxy 127.0.0.1:6080" not in out
    assert out.index("/web-login/") < out.index("handle /*")


def test_static_pages_are_brace_free():
    # Caddy `respond` treats {...} as placeholders, so the preparing/closed pages must be
    # brace-free (meta-refresh, not JS/CSS keyframes) or Caddy would mangle them.
    from es.web_login import _PREPPING_PAGE, _CLOSED_PAGE
    for page in (_PREPPING_PAGE, _CLOSED_PAGE):
        assert "{" not in page and "}" not in page


def test_prepping_then_arm_swaps_to_novnc():
    armed = add_route_block(prepping_route_block(CADDY))
    assert "reverse_proxy 127.0.0.1:6080" in armed
    assert "Preparing your login" not in armed


def test_prepping_then_close_swaps_to_closed_page():
    closed = close_route_block(prepping_route_block(CADDY))
    assert "Login window isn't active" in closed
    assert "Preparing your login" not in closed


from es.web_login import has_session_cookies

# Cheap NECESSARY pre-check (not sufficient — cookies can be stale): the durable Google
# session-anchor cookies. Absent => definitely signed out, so skip the live probe entirely
# and don't bother warm-keeping. Present => "seem in order" => confirm with the live probe.


def test_has_session_cookies_true_with_1psid_anchor():
    assert has_session_cookies({"cookies": [{"name": "NID"}, {"name": "__Secure-1PSID"}]}) is True


def test_has_session_cookies_true_with_3psid():
    assert has_session_cookies({"cookies": [{"name": "__Secure-3PSID"}]}) is True


def test_has_session_cookies_false_without_anchor():
    assert has_session_cookies({"cookies": [{"name": "NID"}, {"name": "CONSENT"}]}) is False


def test_has_session_cookies_false_empty_or_missing():
    assert has_session_cookies({"cookies": []}) is False
    assert has_session_cookies({}) is False
    assert has_session_cookies(None) is False


from es.web_login import run_es_login


class _Spy:
    def __init__(self, home):
        self.home = home
        self.probed = self.captured = self.opened = self.closed = False
    def probe_home(self, p): self.probed = True; return self.home
    def capture(self, p): self.captured = True
    def open_signin(self, p): self.opened = True
    def close_window(self): self.closed = True
    def run(self, p="google"):
        return run_es_login(p, probe_home=self.probe_home, capture=self.capture,
                            open_signin=self.open_signin, close_window=self.close_window,
                            login_url="https://x/web-login/")


def test_signed_in_captures_and_closes_window():
    s = _Spy({"acct": True, "signin": False})
    out = s.run()
    assert out["status"] == "logged_in" and s.captured and s.closed and not s.opened


def test_signed_out_opens_window_and_returns_link():
    s = _Spy({"acct": False, "signin": True})
    out = s.run()
    assert out["status"] == "awaiting_login" and s.opened and not s.captured
    assert out["login_url"] == "https://x/web-login/"


def test_probe_always_runs_no_cheap_pre_gate():
    # regression: never short-circuit before the probe — it restores the session after a restart
    s = _Spy({"acct": True, "signin": False})
    s.run()
    assert s.probed



from es.web_login import run_warm_keep


class _WarmSpy:
    def __init__(self, durable, home):
        self.durable, self.home = durable, home
        self.probed = self.persisted = False
    def read_durable(self, p): return self.durable
    def probe_home(self, p): self.probed = True; return self.home
    def persist(self, p): self.persisted = True
    def run(self, p="google"):
        return run_warm_keep(p, read_durable=self.read_durable, probe_home=self.probe_home,
                             persist=self.persist)


def test_warm_keep_skips_profile_with_no_stored_session():
    s = _WarmSpy({"cookies": []}, {"acct": True, "signin": False})
    out = s.run()
    assert out["warmed"] is False and not s.probed  # cheap durable pre-gate; no browse


def test_warm_keep_touches_and_persists_when_session_stored():
    s = _WarmSpy({"cookies": [{"name": "__Secure-1PSID"}]}, {"acct": True, "signin": False})
    out = s.run()
    assert out["warmed"] is True and out["signed_in"] is True and s.probed and s.persisted


def test_warm_keep_reports_dead_session_but_never_triggers_login():
    # stored cookies but the live probe says signed-out (session died server-side):
    # report it, keep the touch, but do NOT open a login window — only a tool use may do that.
    s = _WarmSpy({"cookies": [{"name": "__Secure-1PSID"}]}, {"acct": False, "signin": True})
    out = s.run()
    assert out["warmed"] is True and out["signed_in"] is False and "login_url" not in out


# --- Instance routing: es tools MUST drive camofox-auth, never the login-less flex ---------
# The two Camoufox instances are deliberately split (flex :9377 = Hermes browser_*,
# login-less; auth :9378 = the authenticated session). es owns the authenticated one, so it
# reads CAMOFOX_AUTH_URL. Reading CAMOFOX_URL here would point es at the flex instance, where
# no login exists — silently breaking es_login and the warm-keeper.
def test_web_login_uses_auth_url(monkeypatch):
    import importlib
    monkeypatch.setenv("CAMOFOX_AUTH_URL", "http://localhost:9378")
    monkeypatch.setenv("CAMOFOX_URL", "http://localhost:9377")
    import es.web_login as wl
    importlib.reload(wl)
    assert wl._CAMOFOX == "http://localhost:9378"


def test_web_login_defaults_to_9378_not_flex(monkeypatch):
    import importlib
    monkeypatch.delenv("CAMOFOX_AUTH_URL", raising=False)
    monkeypatch.setenv("CAMOFOX_URL", "http://localhost:9377")
    import es.web_login as wl
    importlib.reload(wl)
    assert wl._CAMOFOX == "http://localhost:9378"


def test_probe_home_closes_its_tab(monkeypatch):
    """probe_home leaked a tab on EVERY call. run_warm_keep calls it every 6h,
    so EverStone abandoned four live google.com pages a day — camofox RSS grew
    938MB -> 2013MB with 8 orphaned tabs, taking the VM to 159MB free. The leak
    was invisible because GET /tabs returns [] without a ?userId param.
    """
    closed = []
    monkeypatch.setattr(wl, "_navigate", lambda p, u: {"tabId": "T1"})
    monkeypatch.setattr(wl, "_evaluate", lambda p, t, e: {"acct": True})
    monkeypatch.setattr(wl.httpx, "post", lambda *a, **k: None)
    monkeypatch.setattr(wl.httpx, "delete",
                        lambda url, **k: closed.append((url, k.get("params"))))
    assert wl.probe_home("google") == {"acct": True}
    assert closed and closed[0][0].endswith("/tabs/T1")
    assert closed[0][1] == {"userId": "google"}


# --- The window supervisor: /web-login/ must always tell the truth --------------------------
# The vnc watcher restarts x11vnc whenever Camoufox's display changes, and x11vnc exits when its
# X server goes away, so the noVNC path can serve, drop, and come back — during startup AND long
# after. Arming on the first successful probe published a link into that gap, and a one-shot arm
# then left the route pointing at a dead pipe for the rest of the window; either way the operator
# got "Failed to connect to server." with nothing to act on. The real chain is exercised in
# e2e/test_web_login_vnc.py — these pin the DECISIONS: what a sequence of probes arms on, and
# what it takes the route back down.

READY, DOWN = (True, ":0"), (False, None)
# stable_for/interval below => 6 consecutive passes on one display are required to arm.


def _run_window(monkeypatch, samples, run_for=0.5, arm_timeout=0.2, grace=3):
    """Drive _run_window over a scripted probe sequence; return the route swaps it made.
    Past the end of the script the server reads as down, so only the script can arm."""
    swaps, seq = [], list(samples)
    sample = lambda: seq.pop(0) if seq else DOWN  # noqa: E731
    monkeypatch.setattr(wl, "_swap_route", lambda fn: swaps.append(fn.__name__))
    monkeypatch.setattr(wl, "_vnc_ready", sample)
    monkeypatch.setattr(wl, "_rfb_over_websockify", lambda *a, **k: sample()[0])
    wl._run_window(wl._next_generation(), interval=0.01, stable_for=0.05,
                   arm_timeout=arm_timeout, watch_every=0.01, grace=grace, lifetime=run_for)
    time.sleep(run_for + 0.2)  # past the worker's own lifetime, so its verdict is final
    return swaps


def test_arm_requires_continuous_readiness(monkeypatch):
    assert _run_window(monkeypatch, [READY] * 6)[0] == "add_route_block"


def test_arm_ignores_a_single_lucky_probe(monkeypatch):
    # One pass, then the server drops (x11vnc restarting): the pass must not arm, and the streak
    # must restart from zero — the 5 that follow are not enough. It gives up instead of arming.
    assert _run_window(monkeypatch, [READY, DOWN] + [READY] * 5) == ["close_route_block"]


def test_arm_resets_the_streak_when_the_display_changes(monkeypatch):
    # A display change means the browser restarted onto a new Xvfb and x11vnc was restarted with
    # it — the old streak proves nothing about the new server, so it must not count toward arming.
    assert _run_window(monkeypatch, [READY] * 5 + [(True, ":1")] * 2) == ["close_route_block"]


def test_arm_gives_up_to_the_closed_page(monkeypatch):
    # Never armed within the timeout: fall back to the static page, or the preparing page would
    # meta-refresh forever.
    assert _run_window(monkeypatch, [DOWN] * 500) == ["close_route_block"]


def test_a_dead_pipe_takes_the_route_back_down(monkeypatch):
    # The regression that reaches the operator: the browser is reaped/killed/restarted AFTER the
    # link was sent. The route must stop serving noVNC instead of leaving a dead pipe behind it.
    swaps = _run_window(monkeypatch, [READY] * 6 + [DOWN] * 3)
    assert swaps[:2] == ["add_route_block", "prepping_route_block"]


def test_a_single_missed_probe_does_not_disarm(monkeypatch):
    # One blip is not a dead window — disarming on it would bounce a working login back to the
    # preparing page for no reason.
    swaps = _run_window(monkeypatch, [READY] * 6 + [DOWN] + [READY] * 40, run_for=0.3)
    assert swaps == ["add_route_block"]


def test_a_window_that_recovers_gets_re_armed(monkeypatch):
    # x11vnc restarted onto a new display: preparing page while it's gone, live noVNC once it is
    # back and stable. The operator's page self-refreshes across the whole dip.
    swaps = _run_window(monkeypatch, [READY] * 6 + [DOWN] * 3 + [(True, ":1")] * 8)
    assert swaps[:3] == ["add_route_block", "prepping_route_block", "add_route_block"]


def test_a_newer_call_supersedes_a_pending_window(monkeypatch):
    swaps = []
    monkeypatch.setattr(wl, "_swap_route", lambda fn: swaps.append(fn.__name__))
    monkeypatch.setattr(wl, "_vnc_ready", lambda: READY)
    stale = wl._next_generation()
    wl._next_generation()  # a later open_signin/close claims the window
    wl._run_window(stale, interval=0.01, stable_for=0.05, arm_timeout=0.2, lifetime=0.3)
    time.sleep(0.3)
    assert swaps == [], "a superseded window must not touch the route"


def test_the_window_beats_the_login_tab_while_it_is_open(monkeypatch):
    # VNC keystrokes never reach camofox's API, so without this the tab looks abandoned from the
    # moment it opens and the reapers take the browser out from under the operator.
    beats = []
    monkeypatch.setattr(wl, "_swap_route", lambda fn: None)
    monkeypatch.setattr(wl, "_vnc_ready", lambda: READY)
    monkeypatch.setattr(wl, "_rfb_over_websockify", lambda *a, **k: True)
    monkeypatch.setattr(wl, "_beat", lambda p, t: beats.append((p, t)))
    monkeypatch.setattr(wl, "_KEEPALIVE_INTERVAL", 0.02)
    wl._run_window(wl._next_generation(), "google", "TAB1", interval=0.01, stable_for=0.02,
                   arm_timeout=0.2, watch_every=0.01, lifetime=0.2)
    time.sleep(0.4)
    assert beats and beats[0] == ("google", "TAB1")


def test_no_tab_means_no_beats(monkeypatch):
    beats = []
    monkeypatch.setattr(wl, "_swap_route", lambda fn: None)
    monkeypatch.setattr(wl, "_vnc_ready", lambda: READY)
    monkeypatch.setattr(wl, "_rfb_over_websockify", lambda *a, **k: True)
    monkeypatch.setattr(wl, "_beat", lambda p, t: beats.append((p, t)))
    monkeypatch.setattr(wl, "_KEEPALIVE_INTERVAL", 0.02)
    wl._run_window(wl._next_generation(), "google", None, interval=0.01, stable_for=0.02,
                   arm_timeout=0.2, watch_every=0.01, lifetime=0.2)
    time.sleep(0.4)
    assert beats == []


def test_probe_home_closes_its_tab_even_when_evaluate_raises(monkeypatch):
    """The close must be in a finally — a failing probe is exactly when the
    retry loop runs again, so a leak on the error path compounds fastest."""
    closed = []
    monkeypatch.setattr(wl, "_navigate", lambda p, u: {"tabId": "T2"})
    def boom(*a, **k):
        raise RuntimeError("evaluate failed")
    monkeypatch.setattr(wl, "_evaluate", boom)
    monkeypatch.setattr(wl.httpx, "post", lambda *a, **k: None)
    monkeypatch.setattr(wl.httpx, "delete", lambda url, **k: closed.append(url))
    try:
        wl.probe_home("google")
    except RuntimeError:
        pass
    assert closed and closed[0].endswith("/tabs/T2")
