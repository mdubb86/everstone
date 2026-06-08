import pytest
from datetime import datetime, timezone
from es.tasks_client import TasksClient, ParentNotFound, HasSubtasks


@pytest.fixture
def client(radicale):
    c = TasksClient(url=radicale, username="", password=""); c.ensure_list("inbox"); return c


def test_add_list(client):
    uid = client.add_task("Buy milk", list_name="inbox")
    t = client.list_tasks("inbox")[0]
    assert t["uid"] == uid and t["summary"] == "Buy milk" and t["status"] == "NEEDS-ACTION"


def test_add_url(client):
    uid = client.add_task("Read", list_name="inbox", url="obsidian://open?vault=v&file=a.md")
    assert client.list_tasks("inbox")[0]["url"] == "obsidian://open?vault=v&file=a.md"


def test_complete(client):
    uid = client.add_task("X", list_name="inbox"); client.complete_task(uid, list_name="inbox")
    assert client.list_tasks("inbox")[0]["status"] == "COMPLETED"


def test_set_link(client):
    uid = client.add_task("X", list_name="inbox")
    client.set_note_link(uid, list_name="inbox", url="obsidian://open?vault=v&file=n.md")
    assert client.list_tasks("inbox")[0]["url"].endswith("n.md")


def test_alarm_persisted(client):
    uid = client.add_task("Ring", list_name="inbox",
                          remind_at=datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc))
    assert client.list_tasks("inbox")[0]["has_alarm"] is True


def test_list_tasks_parent_none_by_default(client):
    client.add_task("Standalone", list_name="inbox")
    assert client.list_tasks("inbox")[0]["parent"] is None


def test_find_in_any_list_locates_uid(client):
    client.ensure_list("other")
    uid = client.add_task("Findme", list_name="other")
    todo, found_list = client._find_in_any_list(uid)
    assert found_list == "other"
    assert str(todo.icalendar_component.get("uid")) == uid


def test_find_in_any_list_raises_when_missing(client):
    with pytest.raises(ParentNotFound):
        client._find_in_any_list("nope-not-here")
