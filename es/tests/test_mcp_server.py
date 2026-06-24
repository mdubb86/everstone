from unittest.mock import MagicMock
import pytest
from es import mcp_server
from es.tasks_client import ParentNotFound


@pytest.fixture
def fake_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("es.mcp_server._client", lambda: (client, "VaultName"))
    return client


def test_es_tasks_list_ok(fake_client):
    fake_client.list_tasks.return_value = [
        {"uid": "u1", "summary": "milk", "status": "NEEDS-ACTION"}]
    out = mcp_server.es_tasks_list(list="inbox")
    assert out == {"ok": True, "data": [
        {"uid": "u1", "summary": "milk", "status": "NEEDS-ACTION"}]}
    fake_client.list_tasks.assert_called_once_with("inbox")


def test_es_tasks_list_filters_completed(fake_client):
    fake_client.list_tasks.return_value = [
        {"uid": "u1", "status": "NEEDS-ACTION"}, {"uid": "u2", "status": "COMPLETED"}]
    out = mcp_server.es_tasks_list(list="inbox")
    assert [t["uid"] for t in out["data"]] == ["u1"]


def test_mcp_envelope_catches_es_code():
    @mcp_server.mcp_envelope
    def boom():
        raise ParentNotFound("nope")
    assert boom() == {"ok": False, "error": {"code": "parent_not_found", "message": "nope"}}
