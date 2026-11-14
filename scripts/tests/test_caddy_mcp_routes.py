import configure


def test_block_routes_oauth_and_mcp_to_9400():
    block = configure.mcp_caddy_block()
    assert "reverse_proxy localhost:9400" in block
    for p in ("/mcp", "/authorize", "/token", "/register",
              "/.well-known/oauth-authorization-server",
              "/.well-known/oauth-protected-resource", "/revoke"):
        assert p in block


def test_rendered_caddyfile_includes_block_only_when_enabled(tmp_path, monkeypatch):
    tmpl = "x " + configure.MCP_ROUTES_MARKER + " y"
    assert configure.render_caddyfile(tmpl, {"public_url": "https://e.test"}) == "x  y"
    enabled = {"public_url": "https://e.test", "mcp_http": {"enabled": True, "consent_password": "p"}}
    out = configure.render_caddyfile(tmpl, enabled)
    assert "reverse_proxy localhost:9400" in out and configure.MCP_ROUTES_MARKER not in out
