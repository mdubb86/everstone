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


def test_add_with_parent_sets_related_to_in_parent_list(client):
    client.ensure_list("proj")
    parent = client.add_task("Beach trip", list_name="proj")
    # --list is ignored when parent is given: child lands in the parent's list
    child = client.add_task("Book hotel", list_name="inbox", parent_uid=parent)
    items = client.list_tasks("proj")
    child_item = next(t for t in items if t["uid"] == child)
    assert child_item["parent"] == parent
    # nothing leaked into the passed (ignored) list
    assert all(t["uid"] != child for t in client.list_tasks("inbox"))


def test_add_with_unknown_parent_raises(client):
    with pytest.raises(ParentNotFound):
        client.add_task("Orphan", list_name="inbox", parent_uid="does-not-exist")


def test_edit_set_parent(client):
    parent = client.add_task("Parent", list_name="inbox")
    child = client.add_task("Child", list_name="inbox")
    client.edit_task(child, "inbox", parent_uid=parent)
    item = next(t for t in client.list_tasks("inbox") if t["uid"] == child)
    assert item["parent"] == parent


def test_edit_detach_parent(client):
    parent = client.add_task("Parent", list_name="inbox")
    child = client.add_task("Child", list_name="inbox", parent_uid=parent)
    client.edit_task(child, "inbox", parent_uid="")  # "" detaches
    item = next(t for t in client.list_tasks("inbox") if t["uid"] == child)
    assert item["parent"] is None


def test_edit_parent_none_leaves_link_untouched(client):
    parent = client.add_task("Parent", list_name="inbox")
    child = client.add_task("Child", list_name="inbox", parent_uid=parent)
    client.edit_task(child, "inbox", summary="renamed")  # parent_uid defaults None
    item = next(t for t in client.list_tasks("inbox") if t["uid"] == child)
    assert item["parent"] == parent and item["summary"] == "renamed"
