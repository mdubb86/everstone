import json
from everstone_tasks.cli import main


def _run(args, env, capsys):
    code = main(args, env=env); return code, capsys.readouterr().out


def test_add_list_json(radicale, capsys):
    env = {"EVERSTONE_CALDAV_URL": radicale, "EVERSTONE_VAULT_NAME": "v"}
    _run(["add", "Task A", "--list", "inbox"], env, capsys)
    code, out = _run(["list", "--list", "inbox", "--json"], env, capsys)
    assert code == 0 and [t["summary"] for t in json.loads(out)] == ["Task A"]


def test_add_note_deeplink(radicale, capsys):
    env = {"EVERSTONE_CALDAV_URL": radicale, "EVERSTONE_VAULT_NAME": "myvault"}
    _run(["add", "N", "--list", "inbox", "--note", "Notes/x.md"], env, capsys)
    _, out = _run(["list", "--list", "inbox", "--json"], env, capsys)
    assert json.loads(out)[0]["url"] == "obsidian://open?vault=myvault&file=Notes%2Fx.md"
