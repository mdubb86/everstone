import configure


def test_resolve_mcp_http_disabled_when_absent():
    assert configure.resolve_mcp_http({"public_url": "https://x.test"}) is None


def test_resolve_mcp_http_disabled_flag():
    cfg = {"public_url": "https://x.test", "mcp_http": {"enabled": False, "consent_password": "p"}}
    assert configure.resolve_mcp_http(cfg) is None


def test_resolve_mcp_http_defaults_issuer_from_public_url():
    cfg = {"public_url": "https://everstone.waage.haus/", "mcp_http": {"enabled": True, "consent_password": "p"}}
    got = configure.resolve_mcp_http(cfg)
    assert got == {"enabled": True, "issuer_url": "https://everstone.waage.haus", "consent_password": "p"}


def test_resolve_mcp_http_explicit_issuer_wins():
    cfg = {"public_url": "https://a.test", "mcp_http": {"enabled": True, "issuer_url": "https://b.test", "consent_password": "p"}}
    assert configure.resolve_mcp_http(cfg)["issuer_url"] == "https://b.test"
