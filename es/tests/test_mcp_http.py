import pytest
from starlette.testclient import TestClient
from es.oauth_store import OAuthStore
from es import mcp_http

ISSUER = "https://everstone.waage.haus"


@pytest.fixture
def ctx(tmp_path):
    store = OAuthStore(str(tmp_path / "oauth.db"))
    app = mcp_http.build_app(store, ISSUER, "pw")
    return store, TestClient(app)


def test_protected_resource_metadata_served(ctx):
    _, client = ctx
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    assert ISSUER in r.json().get("authorization_servers", [None])[0] or \
           r.json().get("resource", "").startswith(ISSUER)


def test_mcp_requires_auth(ctx):
    _, client = ctx
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


def test_token_verifier_accepts_valid(ctx):
    store, _ = ctx
    import asyncio
    t = store.issue_tokens("cid", "es")
    v = mcp_http.StoreTokenVerifier(store)
    assert asyncio.run(v.verify_token(t["access_token"])) is not None
    assert asyncio.run(v.verify_token("bad")) is None


def test_as_routes_present(ctx):
    _, client = ctx
    assert client.get("/.well-known/oauth-authorization-server").status_code == 200
