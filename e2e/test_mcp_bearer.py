"""End-to-end: the es-mcp-http endpoint gated by a static bearer token in Caddy.

Drives the real built+booted container: wrong/absent token → 401 (Caddy), correct
token → full MCP session (initialize → tools/list → an authenticated tools/call).
"""
import json
import time

import pytest
import requests

TOKEN = "e2e-mcp-token"  # must match mcp_http.token in conftest._write_config
ACCEPT = "application/json, text/event-stream"


def _auth(override=None):
    h = {"Authorization": f"Bearer {TOKEN}", "Accept": ACCEPT, "Content-Type": "application/json"}
    if override:
        h.update(override)
    return h


def _parse_sse(resp):
    """streamable-http frames the reply as ``event: message\\r\\ndata: {json-rpc}``;
    pull the ``data:`` JSON (fall back to a plain JSON body)."""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"no data: line in SSE body: {resp.text!r}")
    return resp.json()


def _init_body():
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "e2e", "version": "0"}}}


@pytest.fixture(scope="module", autouse=True)
def _mcp_ready(everstone):
    """Wait for the es-mcp-http longrun to be serving behind Caddy — the fixture's
    /health check only proves Caddy is up, not the separate es-mcp-http service."""
    url = everstone["base_url"] + "/mcp"
    for _ in range(45):
        try:
            r = requests.post(url, headers=_auth(), json=_init_body(), timeout=4)
            if r.status_code != 502:
                return
        except Exception:
            pass
        time.sleep(2)
    pytest.fail("es-mcp-http did not come up behind Caddy in time (still 502)")


def test_mcp_no_token_rejected(everstone):
    r = requests.post(everstone["base_url"] + "/mcp",
                      headers={"Accept": ACCEPT, "Content-Type": "application/json"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_mcp_wrong_token_rejected(everstone):
    r = requests.post(everstone["base_url"] + "/mcp",
                      headers=_auth({"Authorization": "Bearer WRONG-TOKEN"}),
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_mcp_bearer_full_flow(everstone):
    """Correct bearer → MCP session → tools/list → authenticated tools/call."""
    b = everstone["base_url"]
    s = requests.Session()
    H = _auth()
    init = s.post(b + "/mcp", headers=H, json=_init_body())
    assert init.status_code == 200
    sid = init.headers.get("mcp-session-id")
    assert sid, "server must issue an mcp-session-id"
    H["mcp-session-id"] = sid
    s.post(b + "/mcp", headers=H, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    tl = s.post(b + "/mcp", headers=H, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tl.status_code == 200
    names = {t["name"] for t in _parse_sse(tl)["result"]["tools"]}
    assert {"es_tasks_lists", "es_tasks_add", "es_notes_journal"} <= names

    call = s.post(b + "/mcp", headers=H, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "es_tasks_lists", "arguments": {}}})
    assert call.status_code == 200
    result = _parse_sse(call)["result"]
    assert result.get("isError") is False
    payload = json.loads(result["content"][0]["text"])  # the es {ok,data} envelope
    assert payload["ok"] is True and isinstance(payload["data"], list)
