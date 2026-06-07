from datetime import datetime

from everstone_tasks.client import TasksClient


def test_list_collections_counts(radicale):
    c = TasksClient(radicale)
    c.add_task("a", "TODO")
    c.add_task("b", "TODO")
    uid = c.add_task("c", "Shopping")
    c.complete_task(uid, "Shopping")
    cols = {x["name"]: x for x in c.list_collections()}
    assert cols["TODO"]["total_count"] == 2
    assert cols["TODO"]["open_count"] == 2
    assert cols["Shopping"]["total_count"] == 1
    assert cols["Shopping"]["open_count"] == 0  # the one task is completed


def test_clear_list_completed_only(radicale):
    c = TasksClient(radicale)
    keep = c.add_task("not bought", "Groceries")
    bought = c.add_task("bought", "Groceries")
    c.complete_task(bought, "Groceries")
    removed = c.clear_list("Groceries")  # completed_only default
    assert removed == 1
    names = [t["summary"] for t in c.list_tasks("Groceries")]
    assert names == ["not bought"]


def test_clear_list_all(radicale):
    c = TasksClient(radicale)
    c.add_task("x", "Beach"); c.add_task("y", "Beach")
    removed = c.clear_list("Beach", completed_only=False)
    assert removed == 2
    assert c.list_tasks("Beach") == []


def test_delete_list_removes_collection(radicale):
    c = TasksClient(radicale)
    c.add_task("x", "Beach")
    c.delete_list("Beach")
    assert "Beach" not in [x["name"] for x in c.list_collections()]


def test_add_with_tags_and_due_roundtrips(radicale):
    c = TasksClient(radicale)
    c.add_task("tagged", "TODO", tags=["errand", "town"],
               due=datetime(2026, 6, 10, 17, 0))
    t = c.list_tasks("TODO")[0]
    assert set(t["tags"]) == {"errand", "town"}
    assert t["due"] is not None and t["due"].startswith("2026-06-10")


def test_edit_task_updates_fields(radicale):
    c = TasksClient(radicale)
    uid = c.add_task("draft", "TODO", tags=["old"])
    c.edit_task(uid, "TODO", summary="final", due=datetime(2026, 6, 11, 9, 0), tags=["new"])
    t = [x for x in c.list_tasks("TODO") if x["uid"] == uid][0]
    assert t["summary"] == "final"
    assert t["tags"] == ["new"]
    assert t["due"].startswith("2026-06-11")


def test_edit_task_replaces_reminder(radicale):
    c = TasksClient(radicale)
    uid = c.add_task("ping", "TODO", remind_at=datetime(2026, 6, 11, 8, 0))
    c.edit_task(uid, "TODO", remind_at=datetime(2026, 6, 12, 8, 0))
    t = [x for x in c.list_tasks("TODO") if x["uid"] == uid][0]
    assert t["has_alarm"] is True  # still exactly one alarm, replaced not duplicated
