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


def test_es_tasks_delete_ok(fake_client):
    out = mcp_server.es_tasks_delete("u1", list="inbox", force=True)
    assert out == {"ok": True, "data": {"uid": "u1", "deleted": True}}
    fake_client.delete_task.assert_called_once_with("u1", "inbox", force=True)


def test_es_tasks_delete_default(fake_client):
    mcp_server.es_tasks_delete("u1")
    fake_client.delete_task.assert_called_once_with("u1", "TODO", force=False)


def test_es_tasks_delete_has_subtasks(fake_client):
    fake_client.delete_task.side_effect = HasSubtasks("has kids")
    out = mcp_server.es_tasks_delete("u1")
    assert out == {"ok": False, "error": {"code": "has_subtasks", "message": "has kids"}}


def test_es_tasks_lists_ok(fake_client):
    fake_client.list_collections.return_value = ["TODO", "inbox"]
    out = mcp_server.es_tasks_lists()
    assert out == {"ok": True, "data": ["TODO", "inbox"]}
    fake_client.list_collections.assert_called_once_with()


def test_es_tasks_list_create_ok(fake_client):
    out = mcp_server.es_tasks_list_create("groceries")
    assert out == {"ok": True, "data": {"list": "groceries", "created": True}}
    fake_client.ensure_list.assert_called_once_with("groceries")


def test_es_tasks_list_delete_ok(fake_client):
    out = mcp_server.es_tasks_list_delete("groceries")
    assert out == {"ok": True, "data": {"list": "groceries", "deleted": True}}
    fake_client.delete_list.assert_called_once_with("groceries")


def test_es_tasks_clear_default(fake_client):
    fake_client.clear_list.return_value = 3
    out = mcp_server.es_tasks_clear(list="inbox")
    assert out == {"ok": True, "data": {"list": "inbox", "removed": 3}}
    fake_client.clear_list.assert_called_once_with("inbox", completed_only=True)


def test_es_tasks_clear_all(fake_client):
    fake_client.clear_list.return_value = 5
    out = mcp_server.es_tasks_clear(list="inbox", all=True)
    assert out == {"ok": True, "data": {"list": "inbox", "removed": 5}}
    fake_client.clear_list.assert_called_once_with("inbox", completed_only=False)


@pytest.fixture
def fake_svc(monkeypatch):
    svc = MagicMock()
    monkeypatch.setattr("es.mcp_server.calendar_service", lambda: svc)
    monkeypatch.setattr("es.capabilities.cal_support.resolve_calendar_id", lambda s, c: "calid")
    monkeypatch.setattr("es.capabilities.cal_support.home_tz", lambda: "America/New_York")
    monkeypatch.setattr("es.capabilities.cal_support.calendar_policy", lambda: (set(), ["Work"]))
    return svc


def _events(items):
    """Wire fake_svc.events().list().execute() to return items."""
    return {"items": items}


def test_es_cal_agenda_ok(fake_svc):
    ev = {"id": "e1", "summary": "Standup",
          "start": {"dateTime": "2026-06-10T09:00:00-04:00"},
          "end": {"dateTime": "2026-06-10T09:30:00-04:00"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([ev])
    out = mcp_server.es_cal_agenda("2026-06-10", "2026-06-11", "Work")
    assert out["ok"] is True
    assert out["data"][0]["id"] == "e1"
    assert out["data"][0]["summary"] == "Standup"
    _, kwargs = fake_svc.events.return_value.list.call_args
    assert kwargs["calendarId"] == "calid"
    assert kwargs["singleEvents"] is True


def test_es_cal_search_ok(fake_svc):
    ev = {"id": "e2", "summary": "Dentist",
          "start": {"dateTime": "2026-06-12T10:00:00-04:00"},
          "end": {"dateTime": "2026-06-12T11:00:00-04:00"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([ev])
    out = mcp_server.es_cal_search("dentist", "Work")
    assert out["ok"] is True
    assert out["data"][0]["id"] == "e2"
    _, kwargs = fake_svc.events.return_value.list.call_args
    assert kwargs["q"] == "dentist"
    assert kwargs["calendarId"] == "calid"


def test_es_cal_conflicts_ok(fake_svc):
    a = {"id": "a", "summary": "A",
         "start": {"dateTime": "2026-06-10T09:00:00-04:00"},
         "end": {"dateTime": "2026-06-10T10:00:00-04:00"}}
    b = {"id": "b", "summary": "B",
         "start": {"dateTime": "2026-06-10T09:30:00-04:00"},
         "end": {"dateTime": "2026-06-10T10:30:00-04:00"}}
    c = {"id": "c", "summary": "C",
         "start": {"dateTime": "2026-06-10T11:00:00-04:00"},
         "end": {"dateTime": "2026-06-10T12:00:00-04:00"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([a, b, c])
    out = mcp_server.es_cal_conflicts("2026-06-10", "2026-06-11", "Work")
    assert out["ok"] is True
    assert len(out["data"]) == 1
    pair = out["data"][0]
    assert pair["a"]["id"] == "a"
    assert pair["b"]["id"] == "b"


def test_es_cal_add_ok(fake_svc):
    fake_svc.events.return_value.insert.return_value.execute.return_value = {"id": "new1"}
    out = mcp_server.es_cal_add("Coffee", "Work", when="2026-06-10 09:00",
                                duration=30, where="Cafe", description="chat",
                                tz="America/New_York")
    assert out == {"ok": True, "data": {"id": "new1", "summary": "Coffee"}}
    _, kwargs = fake_svc.events.return_value.insert.call_args
    body = kwargs["body"]
    assert body["summary"] == "Coffee"
    assert body["location"] == "Cafe"
    assert body["description"] == "chat"
    assert body["start"] == {"dateTime": "2026-06-10T09:00:00", "timeZone": "America/New_York"}
    assert body["end"] == {"dateTime": "2026-06-10T09:30:00", "timeZone": "America/New_York"}


def test_es_cal_add_refused_readonly(fake_svc, monkeypatch):
    monkeypatch.setattr("es.capabilities.cal_support.calendar_policy", lambda: ({"Holidays"}, []))
    out = mcp_server.es_cal_add("X", "Holidays", when="2026-06-10 09:00")
    assert out["ok"] is False
    assert out["error"]["code"] == "read_only_calendar"
    fake_svc.events.return_value.insert.assert_not_called()
