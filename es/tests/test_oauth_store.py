import time
import pytest
from es.oauth_store import OAuthStore

@pytest.fixture
def store(tmp_path):
    return OAuthStore(str(tmp_path / "oauth.db"))

def test_register_and_get_client(store):
    cid = store.register_client(["http://127.0.0.1/callback"])
    c = store.get_client(cid)
    assert c["client_id"] == cid
    assert c["redirect_uris"] == ["http://127.0.0.1/callback"]

def test_code_is_one_time(store):
    store.save_code("c0", "cid", "http://127.0.0.1/callback", "chal", "es")
    got = store.take_code("c0")
    assert got["client_id"] == "cid" and got["code_challenge"] == "chal"
    assert store.take_code("c0") is None  # consumed

def test_issue_and_verify_access(store):
    t = store.issue_tokens("cid", "es")
    assert t["token_type"] == "Bearer" and t["expires_in"] == 3600
    v = store.verify_access(t["access_token"])
    assert v == {"client_id": "cid", "scope": "es"}
    assert store.verify_access("nope") is None

def test_refresh_rotates(store):
    t1 = store.issue_tokens("cid", "es")
    t2 = store.rotate_refresh(t1["refresh_token"])
    assert t2["access_token"] != t1["access_token"]
    assert store.rotate_refresh(t1["refresh_token"]) is None  # old refresh consumed
    assert store.verify_access(t2["access_token"]) == {"client_id": "cid", "scope": "es"}

def test_revoke(store):
    t = store.issue_tokens("cid", "es")
    store.revoke(t["access_token"])
    assert store.verify_access(t["access_token"]) is None
