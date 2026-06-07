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
