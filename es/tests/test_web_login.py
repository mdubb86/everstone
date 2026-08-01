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


from es.web_login import build_login_url, add_route_block, remove_route_block

CADDY = "\thandle /oauth {\n\t\treverse_proxy localhost:8081\n\t}\n\n\thandle /* {\n\t\tredir * /hermes/ 302\n\t}\n"


def test_build_login_url_appends_novnc_path_and_autoconnect():
    assert build_login_url("https://everstone.tail.ts.net") == \
        "https://everstone.tail.ts.net/web-login/vnc.html?path=web-login/websockify&autoconnect=true"


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


def test_remove_route_block_removes_it():
    with_block = add_route_block(CADDY)
    assert "web-login" not in remove_route_block(with_block)


def test_remove_route_block_noop_when_absent():
    assert remove_route_block(CADDY) == CADDY


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
    def __init__(self, state, home):
        self.state, self.home = state, home
        self.probed = self.opened = self.closed = False
    def fetch_state(self, p): return self.state
    def probe_home(self, p): self.probed = True; return self.home
    def open_signin(self, p): self.opened = True
    def close_window(self): self.closed = True
    def run(self, p="maps"):
        return run_es_login(p, fetch_state=self.fetch_state, probe_home=self.probe_home,
                            open_signin=self.open_signin, close_window=self.close_window,
                            login_url="https://x/web-login/")


def test_logged_in_closes_window_and_reports():
    s = _Spy({"cookies": [{"name": "__Secure-1PSID"}]}, {"acct": True, "signin": False})
    out = s.run()
    assert out["status"] == "logged_in" and s.closed and not s.opened


def test_no_cookies_short_circuits_live_probe_and_opens_window():
    s = _Spy({"cookies": []}, {"acct": True, "signin": False})
    out = s.run()
    assert out["status"] == "awaiting_login"
    assert s.opened and not s.probed  # cheap pre-gate skipped the expensive browse
    assert out["login_url"] == "https://x/web-login/"


def test_cookies_present_but_stale_opens_window():
    s = _Spy({"cookies": [{"name": "__Secure-1PSID"}]}, {"acct": False, "signin": True})
    out = s.run()
    assert out["status"] == "awaiting_login" and s.probed and s.opened

