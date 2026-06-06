from unittest.mock import MagicMock
from everstone_tasks.client import TasksClient


def _client_with_todo(uid):
    c = TasksClient.__new__(TasksClient)  # bypass __init__/network
    todo = MagicMock()
    todo.icalendar_component = {"uid": uid}
    cal = MagicMock()
    cal.todos.return_value = [todo]
    c._calendar = lambda list_name: cal
    return c, todo


def test_delete_task_calls_delete():
    c, todo = _client_with_todo("u1")
    c.delete_task("u1", "inbox")
    todo.delete.assert_called_once()


def test_delete_missing_raises_keyerror():
    c, _ = _client_with_todo("u1")
    import pytest
    with pytest.raises(KeyError):
        c.delete_task("nope", "inbox")
