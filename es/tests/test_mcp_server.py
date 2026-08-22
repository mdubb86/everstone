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


def test_es_cal_edit_ok(fake_svc):
    updated = {"id": "e1", "summary": "Updated",
               "start": {"dateTime": "2026-06-10T10:00:00-04:00"},
               "end": {"dateTime": "2026-06-10T11:00:00-04:00"}}
    fake_svc.events.return_value.patch.return_value.execute.return_value = updated
    out = mcp_server.es_cal_edit("e1", "Work", summary="Updated",
                                 when="2026-06-10 10:00", duration=60,
                                 where="Room", description="d", tz="America/New_York")
    assert out["ok"] is True
    assert out["data"]["id"] == "e1"
    assert out["data"]["summary"] == "Updated"
    _, kwargs = fake_svc.events.return_value.patch.call_args
    patch = kwargs["body"]
    assert patch["summary"] == "Updated"
    assert patch["location"] == "Room"
    assert patch["description"] == "d"
    assert patch["start"] == {"dateTime": "2026-06-10T10:00:00", "timeZone": "America/New_York"}
    assert patch["end"] == {"dateTime": "2026-06-10T11:00:00", "timeZone": "America/New_York"}
    assert kwargs["eventId"] == "e1"


def test_es_cal_edit_refused_readonly(fake_svc, monkeypatch):
    monkeypatch.setattr("es.capabilities.cal_support.calendar_policy", lambda: ({"Holidays"}, []))
    out = mcp_server.es_cal_edit("e1", "Holidays", summary="x")
    assert out["error"]["code"] == "read_only_calendar"
    fake_svc.events.return_value.patch.assert_not_called()


def test_es_cal_delete_ok(fake_svc):
    fake_svc.events.return_value.delete.return_value.execute.return_value = {}
    out = mcp_server.es_cal_delete("e1", "Work")
    assert out == {"ok": True, "data": {"id": "e1", "deleted": True}}
    _, kwargs = fake_svc.events.return_value.delete.call_args
    assert kwargs == {"calendarId": "calid", "eventId": "e1"}


def test_es_cal_delete_refused_readonly(fake_svc, monkeypatch):
    monkeypatch.setattr("es.capabilities.cal_support.calendar_policy", lambda: ({"Holidays"}, []))
    out = mcp_server.es_cal_delete("e1", "Holidays")
    assert out["error"]["code"] == "read_only_calendar"
    fake_svc.events.return_value.delete.assert_not_called()


# ── es_notes_* tools ────────────────────────────────────────────────────────

@pytest.fixture
def fake_vault(monkeypatch):
    v = MagicMock()
    monkeypatch.setattr("es.mcp_server._notes_client", lambda: v)
    return v


def test_es_notes_journal_ok(fake_vault):
    fake_vault.write_journal.return_value = {"path": "journal/2026-06-24/Note.md",
                                             "obsidian_deeplink": "obsidian://x"}
    out = mcp_server.es_notes_journal("Note", "body", tags=["t"], topics=["EverStone"], meta=None)
    assert out == {"ok": True, "data": {"path": "journal/2026-06-24/Note.md",
                                        "obsidian_deeplink": "obsidian://x"}}
    fake_vault.write_journal.assert_called_once_with(
        "Note", "body", tags=["t"], topics=["EverStone"], meta=None)


def test_es_notes_topic_ok(fake_vault):
    fake_vault.write_topic.return_value = {"path": "topics/EverStone.md", "created": True}
    out = mcp_server.es_notes_topic("EverStone", body="state")
    assert out["ok"] is True and out["data"]["created"] is True
    fake_vault.write_topic.assert_called_once_with(
        "EverStone", body="state", update=None, category=None)


def test_es_notes_topic_passes_category(fake_vault):
    fake_vault.write_topic.return_value = {"path": "People/Allison.md", "created": True}
    out = mcp_server.es_notes_topic("Allison", body="s", category="People")
    assert out["ok"] is True
    fake_vault.write_topic.assert_called_once_with(
        "Allison", body="s", update=None, category="People")


def test_es_notes_topics_ok(fake_vault):
    fake_vault.list_topics.return_value = ["EverStone", "Home network"]
    out = mcp_server.es_notes_topics(like="home")
    assert out == {"ok": True, "data": ["EverStone", "Home network"]}
    fake_vault.list_topics.assert_called_once_with(like="home")


def test_es_notes_read_ok(fake_vault):
    fake_vault.read_note.return_value = {"path": "topics/X.md", "frontmatter": {}, "body": "b"}
    out = mcp_server.es_notes_read("X")
    assert out["ok"] is True and out["data"]["body"] == "b"


def test_es_notes_read_missing_is_error_envelope(fake_vault):
    from es.vault_client import NoteNotFound
    fake_vault.read_note.side_effect = NoteNotFound("nope")
    out = mcp_server.es_notes_read("nope")
    assert out == {"ok": False, "error": {"code": "note_not_found", "message": "nope"}}


def test_es_notes_list_ok(fake_vault):
    fake_vault.list_journal.return_value = [{"title": "T", "path": "journal/d/T.md"}]
    out = mcp_server.es_notes_list(topic="EverStone", since="2026-06-01", day=None)
    assert out == {"ok": True, "data": [{"title": "T", "path": "journal/d/T.md"}]}
    fake_vault.list_journal.assert_called_once_with(topic="EverStone", since="2026-06-01", day=None)


def test_es_notes_attach_ok(fake_vault):
    fake_vault.attach.return_value = {"path": "Topics/Fridge/Fridge.md",
                                      "obsidian_deeplink": "obsidian://x",
                                      "ref": "![[m.pdf]]", "attachment": "Topics/Fridge/m.pdf"}
    out = mcp_server.es_notes_attach("Fridge", "/cache/m.pdf")
    assert out["ok"] is True and out["data"]["ref"] == "![[m.pdf]]"
    fake_vault.attach.assert_called_once_with("Fridge", "/cache/m.pdf")


def test_es_notes_edit_ok(fake_vault):
    fake_vault.edit_note.return_value = {"path": "Journal/2026-07-04/E/E.md",
                                         "obsidian_deeplink": "obsidian://x", "updated": True}
    out = mcp_server.es_notes_edit("Journal/2026-07-04/E/E.md", append="![[p.jpg]]")
    assert out["ok"] is True and out["data"]["updated"] is True
    fake_vault.edit_note.assert_called_once_with(
        "Journal/2026-07-04/E/E.md", body=None, append="![[p.jpg]]")


def test_es_cal_agenda_uses_start_timezone_not_the_rendered_offset(fake_svc):
    """The California bug, end to end.

    Google RENDERS events in the calendar's zone, so an LA event on a New York
    calendar arrives with a -04:00 offset. Reading that offset makes the view a
    no-op — the event's true zone is `start.timeZone`. Verified against the live
    API: the same event returns -05:00 or -07:00 depending purely on the
    timeZone param.
    """
    ev = {"id": "e1", "summary": "Design review",
          "start": {"dateTime": "2026-06-08T18:00:00-04:00", "timeZone": "America/Los_Angeles"},
          "end": {"dateTime": "2026-06-08T19:00:00-04:00", "timeZone": "America/Los_Angeles"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([ev])
    row = mcp_server.es_cal_agenda("2026-06-08", "2026-06-09", "Family")["data"][0]
    assert row["start"] == "2026-06-08T15:00:00-07:00"          # 3pm Pacific, as experienced
    assert row["tz"] == "America/Los_Angeles"
    assert row["start_home"].startswith("2026-06-08T18:00:00")  # 6pm for a NY operator


def test_es_cal_agenda_omits_home_echo_when_zones_match(fake_svc):
    """No redundant second time for an event already in the home zone — the echo
    exists to disambiguate, not to double every row."""
    ev = {"id": "e1", "summary": "Standup",
          "start": {"dateTime": "2026-06-08T09:00:00-04:00", "timeZone": "America/New_York"},
          "end": {"dateTime": "2026-06-08T09:30:00-04:00", "timeZone": "America/New_York"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([ev])
    row = mcp_server.es_cal_agenda("2026-06-08", "2026-06-09", "Family")["data"][0]
    assert row["start"] == "2026-06-08T09:00:00-04:00"
    assert "start_home" not in row and "end_home" not in row


def test_es_cal_agenda_leaves_times_alone_when_no_zone_is_recorded(fake_svc):
    """start.timeZone is optional for single events. With nothing recorded the
    rendering zone is all we have — don't invent one."""
    ev = {"id": "e1", "summary": "Coffee",
          "start": {"dateTime": "2026-06-08T14:00:00Z"},
          "end": {"dateTime": "2026-06-08T15:00:00Z"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([ev])
    row = mcp_server.es_cal_agenda("2026-06-08", "2026-06-09", "Family")["data"][0]
    assert row["start"] == "2026-06-08T14:00:00+00:00"
    assert "tz" not in row and "start_home" not in row


def test_es_cal_agenda_passes_all_day_events_through(fake_svc):
    ev = {"id": "e1", "summary": "Holiday",
          "start": {"date": "2026-06-08"}, "end": {"date": "2026-06-09"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([ev])
    row = mcp_server.es_cal_agenda("2026-06-08", "2026-06-09", "Family")["data"][0]
    assert row["start"] == "2026-06-08" and "start_home" not in row


def test_es_cal_conflicts_empty_when_disjoint(fake_svc):
    a = {"id": "a", "summary": "A", "start": {"dateTime": "2026-06-08T14:00:00Z"},
         "end": {"dateTime": "2026-06-08T15:00:00Z"}}
    b = {"id": "b", "summary": "B", "start": {"dateTime": "2026-06-08T16:00:00Z"},
         "end": {"dateTime": "2026-06-08T17:00:00Z"}}
    fake_svc.events.return_value.list.return_value.execute.return_value = _events([a, b])
    out = mcp_server.es_cal_conflicts("2026-06-08", "2026-06-09", "Family")
    assert out["data"] == []


# ── es_web_fetch tool ───────────────────────────────────────────────────────

import httpx as _httpx
from types import SimpleNamespace


def _fake_resp(status=200, ctype="text/html; charset=utf-8", text="", url="https://ex.com/a"):
    def raise_for_status():
        if status >= 400:
            raise _httpx.HTTPStatusError("err", request=None, response=None)
    return SimpleNamespace(status_code=status, headers={"content-type": ctype},
                           text=text, url=url, raise_for_status=raise_for_status)


def test_es_web_fetch_ok(monkeypatch):
    html = "<html><head><title>Hello</title></head><body>" + ("word " * 200) + "</body></html>"
    monkeypatch.setattr("es.mcp_server._http_get", lambda u: _fake_resp(text=html))
    out = mcp_server.es_web_fetch("https://ex.com/a")
    assert out["ok"] is True
    assert out["data"]["thin"] is False
    assert out["data"]["text"] and "word" in out["data"]["text"]


def test_es_web_fetch_thin(monkeypatch):
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(text="<html><body>hi</body></html>"))
    out = mcp_server.es_web_fetch("https://ex.com/a")
    assert out["ok"] is True and out["data"]["thin"] is True


def test_es_web_fetch_non2xx_is_error(monkeypatch):
    monkeypatch.setattr("es.mcp_server._http_get", lambda u: _fake_resp(status=403))
    out = mcp_server.es_web_fetch("https://ex.com/a")
    assert out["ok"] is False and "error" in out


def test_es_web_fetch_non_html_skips_extract(monkeypatch):
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(ctype="application/pdf", text="%PDF..."))
    out = mcp_server.es_web_fetch("https://ex.com/a.pdf")
    assert out["ok"] is True and out["data"]["text"] == "" and out["data"]["thin"] is True


def test_web_fetch_blocks_internal_url_end_to_end():
    """No monkeypatch of _http_get — exercises the real wiring. Must fail
    BEFORE any connection is attempted."""
    out = mcp_server.es_web_fetch("http://127.0.0.1:5984/_all_dbs")
    assert out["ok"] is False
    assert out["error"]["code"] == "url_blocked"
    assert "internal" in out["error"]["message"].lower()


def test_http_get_hook_checks_each_redirect_hop(monkeypatch):
    """The guard must run per-request, not once — a public URL redirecting to
    an internal one must still be refused."""
    seen = []
    monkeypatch.setattr("es.mcp_server.url_guard.check_url",
                        lambda u: seen.append(u))
    hook = mcp_server._guard_request_hook
    hook(SimpleNamespace(url="https://public.example.com/a"))
    hook(SimpleNamespace(url="http://127.0.0.1/internal"))
    assert seen == ["https://public.example.com/a", "http://127.0.0.1/internal"]


@pytest.fixture
def fake_people(monkeypatch):
    svc = MagicMock()
    monkeypatch.setattr("es.mcp_server.people_service", lambda: svc)
    return svc


def _set_search_result(svc, results):
    """Wire svc.people().searchContacts(...).execute() to return {"results": results}.

    searchContacts is called more than once (a warm-up empty-query call precedes
    the real query), so .execute() must keep returning the same payload.
    """
    svc.people.return_value.searchContacts.return_value.execute.return_value = {
        "results": results
    }


def test_es_contacts_search_ok(fake_people):
    _set_search_result(fake_people, [{"person": {
        "names": [{"displayName": "Mom"}],
        "phoneNumbers": [{"value": "555-1212"}],
        "emailAddresses": [{"value": "mom@x.com"}],
    }}])
    out = mcp_server.es_contacts_search("mom")
    assert out["ok"] is True
    assert out["data"][0]["name"] == "Mom"
    assert out["data"][0]["phones"] == ["555-1212"]
    assert out["data"][0]["emails"] == ["mom@x.com"]
    assert out["data"][0]["addresses"] == []
    assert out["data"][0]["org"] == ""


def test_es_contacts_search_passes_readmask_and_query(fake_people):
    _set_search_result(fake_people, [])
    mcp_server.es_contacts_search("alice", max_results=5)
    _, kwargs = fake_people.people.return_value.searchContacts.call_args
    assert kwargs["query"] == "alice"
    assert kwargs["pageSize"] == 5
    assert kwargs["readMask"] == (
        "names,phoneNumbers,emailAddresses,addresses,organizations"
    )


def test_es_contacts_search_empty_results_ok(fake_people):
    _set_search_result(fake_people, [])
    out = mcp_server.es_contacts_search("nobody")
    assert out["ok"] is True
    assert out["data"] == []


def test_es_contacts_search_maps_org_and_address(fake_people):
    _set_search_result(fake_people, [{"person": {
        "names": [{"displayName": "Bob Vance"}],
        "addresses": [{"formattedValue": "123 Main St"}],
        "organizations": [{"name": "Vance Refrigeration"}],
    }}])
    out = mcp_server.es_contacts_search("bob")
    assert out["data"][0]["name"] == "Bob Vance"
    assert out["data"][0]["addresses"] == ["123 Main St"]
    assert out["data"][0]["org"] == "Vance Refrigeration"
    assert out["data"][0]["phones"] == []
    assert out["data"][0]["emails"] == []


def test_es_contacts_search_warms_cache(fake_people):
    """A warm-up empty-query call precedes the real query."""
    _set_search_result(fake_people, [])
    mcp_server.es_contacts_search("x")
    calls = fake_people.people.return_value.searchContacts.call_args_list
    assert len(calls) >= 2
    assert calls[0].kwargs["query"] == ""


# ── es_web_fetch: content-type dispatch / text passthrough ─────────────────

ICS_BODY = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Game 1 vs Cedar Park Fury\r\n"
    "DTSTART:20260905T140000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Game 2 vs Round Rock SC\r\n"
    "DTSTART:20260912T160000Z\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_web_fetch_returns_calendar_body(monkeypatch):
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(ctype="text/calendar; charset=utf-8",
                                             text=ICS_BODY))
    out = mcp_server.es_web_fetch("https://ex.com/cal.ics")
    assert out["ok"] is True
    d = out["data"]
    assert "BEGIN:VCALENDAR" in d["text"]
    assert d["text"].count("BEGIN:VEVENT") == 2
    assert d["thin"] is False
    assert d["content_type"].startswith("text/calendar")


@pytest.mark.parametrize("ctype", [
    "text/plain", "text/csv", "text/markdown",
    "application/json", "application/xml", "application/atom+xml",
])
def test_web_fetch_returns_text_ish_bodies(ctype, monkeypatch):
    body = "hello " * 100
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(ctype=ctype, text=body))
    out = mcp_server.es_web_fetch("https://ex.com/a")
    assert out["ok"] is True and "hello" in out["data"]["text"]


def test_web_fetch_html_still_uses_trafilatura(monkeypatch):
    html = "<html><head><title>T</title></head><body>" + ("word " * 200) + "</body></html>"
    monkeypatch.setattr("es.mcp_server._http_get", lambda u: _fake_resp(text=html))
    d = mcp_server.es_web_fetch("https://ex.com/a")["data"]
    assert "<html>" not in d["text"] and "word" in d["text"]


def test_web_fetch_empty_text_body_is_thin(monkeypatch):
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(ctype="text/plain", text=""))
    assert mcp_server.es_web_fetch("https://ex.com/a")["data"]["thin"] is True


def test_web_fetch_short_but_complete_feed_is_not_thin(monkeypatch):
    """A 40-line ICS is useful. thin must mean 'empty' for feeds, not 'short'."""
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(ctype="text/calendar", text="BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"))
    assert mcp_server.es_web_fetch("https://ex.com/a")["data"]["thin"] is False


def test_web_fetch_text_body_is_capped(monkeypatch):
    monkeypatch.setattr(mcp_server, "_WEB_FETCH_MAX_BYTES", 50)
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(ctype="text/plain", text="x" * 500))
    assert len(mcp_server.es_web_fetch("https://ex.com/a")["data"]["text"]) <= 50


def test_web_fetch_binary_still_thin_until_phase_2(monkeypatch):
    """PDFs are Phase 2. Until then they keep today's behavior."""
    monkeypatch.setattr("es.mcp_server._http_get",
                        lambda u: _fake_resp(ctype="application/pdf", text="%PDF-1.4"))
    d = mcp_server.es_web_fetch("https://ex.com/a.pdf")["data"]
    assert d["text"] == "" and d["thin"] is True


def test_web_fetch_return_shape_is_stable(monkeypatch):
    """Every branch returns the same keys, so the agent never reasons about
    which are present. cached_path/doc are filled in Phase 2."""
    expected = {"url", "title", "text", "status", "thin", "content_type",
                "cached_path", "doc"}
    for ctype, body in [("text/html", "<html><body>hi</body></html>"),
                        ("text/calendar", ICS_BODY),
                        ("application/pdf", "%PDF-1.4")]:
        monkeypatch.setattr("es.mcp_server._http_get",
                            lambda u, c=ctype, b=body: _fake_resp(ctype=c, text=b))
        assert set(mcp_server.es_web_fetch("https://ex.com/a")["data"]) == expected
