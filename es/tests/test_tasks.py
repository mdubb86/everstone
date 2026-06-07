import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from es import main

runner = CliRunner()


@pytest.fixture
def fake_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("es.capabilities.tasks._client", lambda: (client, "VaultName"))
    return client


def test_list_emits_data(fake_client):
    fake_client.list_tasks.return_value = [{"uid": "u1", "summary": "milk", "status": "NEEDS-ACTION"}]
    res = runner.invoke(main.app, ["tasks", "list", "--list", "inbox"])
    assert res.exit_code == 0
    body = json.loads(res.stdout)
    assert body["ok"] is True
    assert body["data"][0]["summary"] == "milk"
    fake_client.list_tasks.assert_called_once_with("inbox")


def test_add_returns_uid(fake_client):
    fake_client.add_task.return_value = "newuid"
    res = runner.invoke(main.app, ["tasks", "add", "Buy milk", "--list", "inbox"])
    assert res.exit_code == 0
    assert json.loads(res.stdout)["data"] == {"uid": "newuid"}


def test_add_with_note_builds_deeplink(fake_client):
    fake_client.add_task.return_value = "u2"
    runner.invoke(main.app, ["tasks", "add", "Review", "--note", "Projects/Q4.md"])
    _, kwargs = fake_client.add_task.call_args
    assert kwargs["url"] == "obsidian://open?vault=VaultName&file=Projects%2FQ4.md"


def test_done_reports_completed(fake_client):
    res = runner.invoke(main.app, ["tasks", "done", "u1", "--list", "inbox"])
    assert json.loads(res.stdout)["data"] == {"uid": "u1", "status": "COMPLETED"}
    fake_client.complete_task.assert_called_once_with("u1", "inbox")


def test_delete_reports_deleted(fake_client):
    res = runner.invoke(main.app, ["tasks", "delete", "u1"])
    assert json.loads(res.stdout)["data"] == {"uid": "u1", "deleted": True}
    fake_client.delete_task.assert_called_once_with("u1", "TODO")


def test_add_defaults_to_TODO(fake_client):
    fake_client.add_task.return_value = "uid-todo"
    runner.invoke(main.app, ["tasks", "add", "Default list task"])
    args, _ = fake_client.add_task.call_args
    assert args[1] == "TODO"


def test_lists_verb_returns_collections(fake_client):
    fake_client.list_collections.return_value = [{"name": "TODO", "open_count": 1, "total_count": 2}]
    res = runner.invoke(main.app, ["tasks", "lists"])
    assert res.exit_code == 0
    body = json.loads(res.stdout)
    assert body["ok"] is True
    assert body["data"] == [{"name": "TODO", "open_count": 1, "total_count": 2}]


def test_add_forwards_tag_due_remind(fake_client):
    fake_client.add_task.return_value = "uid-x"
    res = runner.invoke(main.app, [
        "tasks", "add", "x",
        "--tag", "a", "--tag", "b",
        "--due", "2026-06-10T09:00",
        "--remind", "2026-06-10T08:30",
    ])
    assert res.exit_code == 0
    kwargs = fake_client.add_task.call_args.kwargs
    assert kwargs["tags"] == ["a", "b"]
    assert kwargs["due"] == datetime(2026, 6, 10, 9, 0)
    assert kwargs["remind_at"] == datetime(2026, 6, 10, 8, 30)


def test_edit_forwards_fields(fake_client):
    res = runner.invoke(main.app, [
        "tasks", "edit", "some-uid",
        "--summary", "new",
        "--due", "2026-06-11T09:00",
    ])
    assert res.exit_code == 0
    kwargs = fake_client.edit_task.call_args.kwargs
    assert kwargs["summary"] == "new"
    assert kwargs["due"] == datetime(2026, 6, 11, 9, 0)


def test_list_create_calls_ensure_list(fake_client):
    res = runner.invoke(main.app, ["tasks", "list-create", "Beach packing"])
    assert res.exit_code == 0
    fake_client.ensure_list.assert_called_once_with("Beach packing")
    body = json.loads(res.stdout)
    assert body["data"] == {"list": "Beach packing", "created": True}


def test_list_delete_calls_delete_list(fake_client):
    res = runner.invoke(main.app, ["tasks", "list-delete", "Beach packing"])
    assert res.exit_code == 0
    fake_client.delete_list.assert_called_once_with("Beach packing")


def test_clear_defaults_completed_only(fake_client):
    res = runner.invoke(main.app, ["tasks", "clear", "🛒 Costco"])
    assert res.exit_code == 0
    fake_client.clear_list.assert_called_once_with("🛒 Costco", completed_only=True)


def test_clear_all_flag(fake_client):
    res = runner.invoke(main.app, ["tasks", "clear", "🛒 Costco", "--all"])
    assert res.exit_code == 0
    fake_client.clear_list.assert_called_once_with("🛒 Costco", completed_only=False)
