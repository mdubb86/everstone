import base64, hashlib
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from es.oauth_store import OAuthStore
from es import oauth_server

ISSUER = "https://everstone.waage.haus"
PW = "sekret"

@pytest.fixture
def client(tmp_path):
    store = OAuthStore(str(tmp_path / "oauth.db"))
    app = Starlette(routes=oauth_server.oauth_routes(store, ISSUER, PW))
    return TestClient(app)

def _pkce():
    verifier = "v" * 64
    chal = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, chal

def test_metadata_lists_endpoints(client):
    m = client.get("/.well-known/oauth-authorization-server").json()
    assert m["issuer"] == ISSUER
    assert m["authorization_endpoint"] == ISSUER + "/authorize"
    assert m["token_endpoint"] == ISSUER + "/token"
    assert m["registration_endpoint"] == ISSUER + "/register"
    assert "S256" in m["code_challenge_methods_supported"]

def test_dcr_registers_client(client):
    r = client.post("/register", json={"redirect_uris": ["http://127.0.0.1/callback"]})
    assert r.status_code in (200, 201)
    assert r.json()["client_id"].startswith("es-")

def test_full_code_pkce_flow(client):
    cid = client.post("/register", json={"redirect_uris": ["http://127.0.0.1/callback"]}).json()["client_id"]
    verifier, chal = _pkce()
    # authorize with correct consent → 302 with code
    r = client.post("/authorize", data={
        "password": PW, "client_id": cid, "redirect_uri": "http://127.0.0.1/callback",
        "code_challenge": chal, "code_challenge_method": "S256", "state": "xyz", "scope": "es",
    }, follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("http://127.0.0.1/callback?") and "state=xyz" in loc
    code = loc.split("code=")[1].split("&")[0]
    # exchange with verifier → tokens
    tok = client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": "http://127.0.0.1/callback", "code_verifier": verifier,
    }).json()
    assert tok["token_type"] == "Bearer" and tok["access_token"].startswith("at-")

def test_wrong_consent_rejected(client):
    cid = client.post("/register", json={"redirect_uris": ["http://127.0.0.1/callback"]}).json()["client_id"]
    _, chal = _pkce()
    r = client.post("/authorize", data={
        "password": "WRONG", "client_id": cid, "redirect_uri": "http://127.0.0.1/callback",
        "code_challenge": chal, "code_challenge_method": "S256", "state": "x", "scope": "es",
    }, follow_redirects=False)
    assert r.status_code == 403

def test_pkce_mismatch_rejected(client):
    cid = client.post("/register", json={"redirect_uris": ["http://127.0.0.1/callback"]}).json()["client_id"]
    _, chal = _pkce()
    r = client.post("/authorize", data={
        "password": PW, "client_id": cid, "redirect_uri": "http://127.0.0.1/callback",
        "code_challenge": chal, "code_challenge_method": "S256", "state": "x", "scope": "es",
    }, follow_redirects=False)
    code = r.headers["location"].split("code=")[1].split("&")[0]
    bad = client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": "http://127.0.0.1/callback", "code_verifier": "WRONG" * 20,
    })
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_grant"

def test_authorize_get_escapes_reflected_params(client):
    malicious = '"><script>alert(1)</script>'
    body = client.get("/authorize", params={"state": malicious}).text
    # the raw attribute-breaking / script payload must NOT appear unescaped
    assert "<script>alert(1)</script>" not in body
    assert '"><script>' not in body
    # it must come back HTML-escaped inside the hidden field
    assert "&lt;script&gt;" in body
    assert "&quot;&gt;" in body


def test_empty_consent_password_never_accepted(tmp_path):
    # Fail-closed: an empty server-side consent password rejects every /authorize
    # POST (including one that sends an empty password), never issuing a code.
    store = OAuthStore(str(tmp_path / "oauth.db"))
    app = Starlette(routes=oauth_server.oauth_routes(store, ISSUER, ""))
    client = TestClient(app)
    cid = client.post("/register", json={"redirect_uris": ["http://127.0.0.1/callback"]}).json()["client_id"]
    _, chal = _pkce()
    base = {
        "client_id": cid, "redirect_uri": "http://127.0.0.1/callback",
        "code_challenge": chal, "code_challenge_method": "S256", "state": "x", "scope": "es",
    }
    # empty password submitted (compare_digest("", "") would be True) → still 403
    r = client.post("/authorize", data={**base, "password": ""}, follow_redirects=False)
    assert r.status_code == 403
    # any non-empty password → also 403 (no password can match an empty server secret)
    r = client.post("/authorize", data={**base, "password": "anything"}, follow_redirects=False)
    assert r.status_code == 403


def test_disallowed_redirect_rejected(client):
    cid = client.post("/register", json={"redirect_uris": ["http://127.0.0.1/callback"]}).json()["client_id"]
    _, chal = _pkce()
    r = client.post("/authorize", data={
        "password": PW, "client_id": cid, "redirect_uri": "https://evil.example/callback",
        "code_challenge": chal, "code_challenge_method": "S256", "state": "x", "scope": "es",
    }, follow_redirects=False)
    assert r.status_code == 400
