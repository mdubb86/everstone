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
