import pytest

from es import mcp_http

CFG = {"public_url": "https://everstone.waage.haus"}


def test_public_host_derived_from_public_url():
    assert mcp_http._public_host(CFG) == "everstone.waage.haus"
    assert mcp_http._public_host({"public_url": "https://everstone.waage.haus/"}) == "everstone.waage.haus"
    assert mcp_http._public_host({}) == ""


def test_build_app_sets_loopback_and_public_host_allowlist():
    mcp_http.build_app(CFG)
    allowed = mcp_http.mcp.settings.transport_security.allowed_hosts
    for h in ("127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*",
              "everstone.waage.haus", "everstone.waage.haus:*"):
        assert h in allowed


def test_build_app_without_public_url_is_loopback_only():
    mcp_http.build_app({})
    allowed = mcp_http.mcp.settings.transport_security.allowed_hosts
    assert allowed == ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]


def test_build_app_serves_mcp_endpoint():
    app = mcp_http.build_app(CFG)
    paths = [getattr(r, "path", None) for r in app.routes]
    # FastMCP's streamable_http_app mounts the MCP transport at /mcp.
    assert any(p == "/mcp" for p in paths)


def test_main_refuses_when_disabled(monkeypatch):
    monkeypatch.setattr(mcp_http.config, "load_config", lambda: {
        "public_url": "https://everstone.waage.haus",
        "mcp_http": {"enabled": False},
    })
    import sys, types
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("uvicorn.run should not be called when disabled"))
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    with pytest.raises(SystemExit) as ei:
        mcp_http.main()
    assert "refusing to start" in str(ei.value)
