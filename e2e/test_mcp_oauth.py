"""FULL end-to-end coverage of the OAuth-protected /mcp endpoint.

Drives the real built+booted container the whole way: OAuth discovery →
Dynamic Client Registration → consent-gated authorize (S256 PKCE) → token →
MCP streamable-http initialize handshake → tools/list → an actual tools/call
whose real {ok:true} envelope must come back. Nothing here is mocked.
"""
import base64, hashlib, json, re, requests


def _parse_sse(resp):
    """Parse an MCP streamable-http reply into its JSON-RPC object.

    The transport frames each response as an SSE event:
    ``event: message\\r\\ndata: {<json-rpc>}\\r\\n\\r\\n``. Pull the ``data:``
    payload and decode it (falling back to a plain JSON body if the server
    ever answers un-framed)."""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"no data: line in SSE body: {resp.text!r}")
    return resp.json()


def _pkce():
    v = "e2e-verifier-" + "x" * 50
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    return v, c


def test_mcp_unauthenticated_challenges(everstone):
    r = requests.post(everstone["base_url"] + "/mcp",
                      headers={"Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


def test_discovery_metadata_served(everstone):
    b = everstone["base_url"]
    assert requests.get(b + "/.well-known/oauth-protected-resource").status_code == 200
    m = requests.get(b + "/.well-known/oauth-authorization-server").json()
    assert m["token_endpoint"].endswith("/token")


def _obtain_token(b):
    """DCR → consent → PKCE code → access token (the full browserless OAuth dance)."""
    cid = requests.post(b + "/register",
                        json={"redirect_uris": ["http://127.0.0.1/callback"]}).json()["client_id"]
    verifier, chal = _pkce()
    r = requests.post(b + "/authorize", data={
        "password": "e2e-consent", "client_id": cid,
        "redirect_uri": "http://127.0.0.1/callback",
        "code_challenge": chal, "code_challenge_method": "S256", "state": "s", "scope": "es",
    }, allow_redirects=False)
    assert r.status_code == 302, r.text
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    return requests.post(b + "/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": "http://127.0.0.1/callback", "code_verifier": verifier,
    }).json()["access_token"]


def test_full_process_initialize_list_and_call(everstone):
    """FULL end-to-end: real built+booted container, OAuth, MCP session handshake,
    tools/list, and an ACTUAL tool call — authenticated the whole way."""
    b = everstone["base_url"]
    tok = _obtain_token(b)
    H = {"Authorization": f"Bearer {tok}",
         "Accept": "application/json, text/event-stream",
         "Content-Type": "application/json"}
    s = requests.Session()
    # 1) initialize → capture the MCP session id
    init = s.post(b + "/mcp", headers=H, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18",
                   "capabilities": {}, "clientInfo": {"name": "e2e", "version": "0"}}})
    assert init.status_code == 200, init.text
    sid = init.headers.get("mcp-session-id")
    assert sid, "server must issue an mcp-session-id"
    H["mcp-session-id"] = sid
    s.post(b + "/mcp", headers=H,
           json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    # 2) tools/list → the full es_* surface is present
    tl = s.post(b + "/mcp", headers=H,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tl.status_code == 200, tl.text
    tools = _parse_sse(tl)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"es_notes_journal", "es_tasks_add", "es_tasks_lists"} <= names, names
    # 3) tools/call a safe read-only tool → a real {ok:true,...} envelope comes back
    call = s.post(b + "/mcp", headers=H, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "es_tasks_lists", "arguments": {}}})
    assert call.status_code == 200, call.text
    result = _parse_sse(call)["result"]
    assert result.get("isError") is False, result
    payload = json.loads(result["content"][0]["text"])  # the es envelope, as a JSON string
    assert payload["ok"] is True, payload
    assert isinstance(payload["data"], list), payload


def test_no_token_cannot_call(everstone):
    """Negative: the same tools/call without a token is rejected (no bypass)."""
    b = everstone["base_url"]
    r = requests.post(b + "/mcp", headers={"Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "es_tasks_lists", "arguments": {}}})
    assert r.status_code == 401


def test_wrong_consent_rejected(everstone):
    b = everstone["base_url"]
    cid = requests.post(b + "/register",
                        json={"redirect_uris": ["http://127.0.0.1/callback"]}).json()["client_id"]
    _, chal = _pkce()
    r = requests.post(b + "/authorize", data={
        "password": "WRONG", "client_id": cid, "redirect_uri": "http://127.0.0.1/callback",
        "code_challenge": chal, "code_challenge_method": "S256", "state": "s", "scope": "es",
    }, allow_redirects=False)
    assert r.status_code == 403
