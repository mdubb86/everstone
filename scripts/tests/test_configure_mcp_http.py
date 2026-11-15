import configure


def test_resolve_mcp_http_disabled_when_absent():
    assert configure.resolve_mcp_http({"public_url": "https://x.test"}) is None


def test_resolve_mcp_http_disabled_flag():
    cfg = {"public_url": "https://x.test", "mcp_http": {"enabled": False, "token": "t"}}
    assert configure.resolve_mcp_http(cfg) is None


def test_resolve_mcp_http_returns_enabled_and_token():
    cfg = {"public_url": "https://everstone.waage.haus/", "mcp_http": {"enabled": True, "token": "s3cret-tok"}}
    assert configure.resolve_mcp_http(cfg) == {"enabled": True, "token": "s3cret-tok"}


def test_resolve_mcp_http_missing_token_is_empty_string():
    cfg = {"public_url": "https://x.test", "mcp_http": {"enabled": True}}
    assert configure.resolve_mcp_http(cfg) == {"enabled": True, "token": ""}
