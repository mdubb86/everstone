from datetime import datetime
from unittest.mock import MagicMock
import pytest
from es import mcp_server
from es.tasks_client import ParentNotFound, HasSubtasks


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


def test_es_tasks_add_ok(fake_client):
    fake_client.add_task.return_value = "newuid"
    out = mcp_server.es_tasks_add(
        "buy milk", list="inbox", note="MyNote", tag="shopping",
        due="2026-06-10T09:00:00", remind="2026-06-09T18:00:00", parent="p1")
    assert out == {"ok": True, "data": {"uid": "newuid"}}
    args, kwargs = fake_client.add_task.call_args
    assert args == ("buy milk", "inbox")
    assert kwargs["url"] == mcp_server.build_deeplink("VaultName", "MyNote")
    assert kwargs["due"] == datetime.fromisoformat("2026-06-10T09:00:00")
    assert kwargs["remind_at"] == datetime.fromisoformat("2026-06-09T18:00:00")
    assert kwargs["tags"] == ["shopping"]
    assert kwargs["parent_uid"] == "p1"


def test_es_tasks_add_minimal(fake_client):
    fake_client.add_task.return_value = "u"
    out = mcp_server.es_tasks_add("plain")
    assert out == {"ok": True, "data": {"uid": "u"}}
    args, kwargs = fake_client.add_task.call_args
    assert args == ("plain", "TODO")
    assert kwargs["url"] is None
    assert kwargs["due"] is None
    assert kwargs["remind_at"] is None
    assert kwargs["tags"] is None
    assert kwargs["parent_uid"] is None


def test_es_tasks_add_parent_not_found(fake_client):
    fake_client.add_task.side_effect = ParentNotFound("no such parent")
    out = mcp_server.es_tasks_add("x", parent="bad")
    assert out == {"ok": False, "error": {"code": "parent_not_found", "message": "no such parent"}}


def test_es_tasks_edit_ok(fake_client):
    out = mcp_server.es_tasks_edit(
        "u1", list="inbox", summary="new", tag="t",
        due="2026-06-10T09:00:00", remind="2026-06-09T18:00:00", parent="p1")
    assert out == {"ok": True, "data": {"uid": "u1", "edited": True}}
    args, kwargs = fake_client.edit_task.call_args
    assert args == ("u1", "inbox")
    assert kwargs["summary"] == "new"
    assert kwargs["due"] == datetime.fromisoformat("2026-06-10T09:00:00")
    assert kwargs["remind_at"] == datetime.fromisoformat("2026-06-09T18:00:00")
    assert kwargs["tags"] == ["t"]
    assert kwargs["parent_uid"] == "p1"


def test_es_tasks_edit_minimal(fake_client):
    out = mcp_server.es_tasks_edit("u1")
    assert out == {"ok": True, "data": {"uid": "u1", "edited": True}}
    args, kwargs = fake_client.edit_task.call_args
    assert args == ("u1", "TODO")
    assert kwargs["summary"] is None
    assert kwargs["due"] is None
    assert kwargs["remind_at"] is None
    assert kwargs["tags"] is None
    assert kwargs["parent_uid"] is None


def test_es_tasks_done_ok(fake_client):
    out = mcp_server.es_tasks_done("u1", list="inbox")
    assert out == {"ok": True, "data": {"uid": "u1", "status": "COMPLETED"}}
    fake_client.complete_task.assert_called_once_with("u1", "inbox")
