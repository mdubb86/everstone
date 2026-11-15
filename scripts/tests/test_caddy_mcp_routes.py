import configure


def test_block_gates_mcp_with_bearer_and_proxies_9400():
    block = configure.mcp_caddy_block("tok123")
    assert "handle /mcp* {" in block
    assert '@noauth not header Authorization "Bearer tok123"' in block
    assert "respond @noauth 401" in block
    assert "reverse_proxy localhost:9400" in block
    assert "flush_interval -1" in block


def test_block_has_no_oauth_paths():
    block = configure.mcp_caddy_block("tok123")
    for p in ("/authorize", "/token", "/register", "/revoke", "/.well-known"):
        assert p not in block


def test_rendered_caddyfile_injects_only_when_enabled():
    tmpl = "x " + configure.MCP_ROUTES_MARKER + " y"
    # disabled → marker stripped to empty
    assert configure.render_caddyfile(tmpl, {"public_url": "https://e.test"}) == "x  y"
    # enabled → block injected with the configured token, marker gone
    enabled = {"public_url": "https://e.test", "mcp_http": {"enabled": True, "token": "abc"}}
    out = configure.render_caddyfile(tmpl, enabled)
    assert 'not header Authorization "Bearer abc"' in out
    assert "reverse_proxy localhost:9400" in out
    assert configure.MCP_ROUTES_MARKER not in out
