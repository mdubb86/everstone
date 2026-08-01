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
