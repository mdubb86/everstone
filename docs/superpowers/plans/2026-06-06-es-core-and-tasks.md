# es Core + Tasks — Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `es` agent CLI skeleton (Typer registry, `config.yaml` loader, JSON envelope) and the first capability, `es tasks`, reusing the existing `TasksClient` CalDAV library.

**Architecture:** New Python package `es/` (Typer app). `main.py` mounts capability sub-apps explicitly. A root callback provides `--pretty`. Each verb returns plain data; an `@envelope` decorator wraps it into `{"ok":true,"data":…}` / `{"ok":false,"error":{…}}` and sets the exit code. `es` reads `/opt/config.yaml` directly (no envdir). `es tasks` imports `everstone_tasks.client.TasksClient` + `everstone_tasks.deeplink` in-process.

**Tech Stack:** Python 3.12, Typer, PyYAML, `everstone_tasks` (caldav). Tests: pytest + Typer's `CliRunner`, monkeypatching `TasksClient`.

**Plans after this one:** Plan 2 = `es cal` + shared Google auth; Plan 3 = cutover (access_hook, configure/services/Dockerfile, AGENTS.md + skill, deprecate old binaries). See `docs/superpowers/specs/2026-06-06-es-tool-gateway-cli-design.md`.

---

### Task 1: Package skeleton + JSON output envelope

**Files:**
- Create: `es/pyproject.toml`
- Create: `es/es/__init__.py`
- Create: `es/es/output.py`
- Test: `es/tests/test_output.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_output.py`:
```python
import json
from es import output


def test_emit_success_envelope(capsys):
    output.emit({"uid": "abc"}, pretty=False)
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": True, "data": {"uid": "abc"}}


def test_emit_error_envelope_and_exit_code(capsys):
    rc = output.emit_error("not_found", "no such task", pretty=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert json.loads(out) == {
        "ok": False,
        "error": {"code": "not_found", "message": "no such task"},
    }


def test_pretty_is_indented(capsys):
    output.emit({"a": 1}, pretty=True)
    assert "\n  " in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_output.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'es.output'`

- [ ] **Step 3: Create the package files**

`es/es/__init__.py`:
```python
```
(empty file)

`es/es/output.py`:
```python
"""Uniform JSON output envelope for the es CLI.

Success: {"ok": true, "data": ...}
Failure: {"ok": false, "error": {"code": "...", "message": "..."}}
The agent parses these; --pretty indents the same JSON for humans.
"""
import json


def _print(obj: dict, pretty: bool) -> None:
    print(json.dumps(obj, indent=2 if pretty else None, default=str))


def emit(data, pretty: bool = False) -> int:
    _print({"ok": True, "data": data}, pretty)
    return 0


def emit_error(code: str, message: str, pretty: bool = False) -> int:
    _print({"ok": False, "error": {"code": code, "message": message}}, pretty)
    return 1
```

`es/pyproject.toml`:
```toml
[project]
name = "es"
version = "0.1.0"
description = "EverStone agent tool-gateway CLI"
requires-python = ">=3.12"
dependencies = ["typer>=0.12", "pyyaml>=6", "everstone-tasks"]

[project.scripts]
es = "es.main:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["es*"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_output.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add es/pyproject.toml es/es/__init__.py es/es/output.py es/tests/test_output.py
git commit -m "feat(es): package skeleton + JSON output envelope"
```

---

### Task 2: Config loader (reads /opt/config.yaml directly)

**Files:**
- Create: `es/es/config.py`
- Test: `es/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_config.py`:
```python
import pytest
from es import config


def test_load_reads_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("caldav:\n  user: alice\n  password: secret\nobsidian:\n  vault_name: Vault\n")
    monkeypatch.setenv("ES_CONFIG_PATH", str(cfg))
    data = config.load_config()
    assert data["caldav"]["user"] == "alice"
    assert data["obsidian"]["vault_name"] == "Vault"


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ES_CONFIG_PATH", str(tmp_path / "nope.yaml"))
    with pytest.raises(FileNotFoundError):
        config.load_config()


def test_caldav_url_is_the_radicale_constant():
    assert config.CALDAV_URL == "http://localhost:5232"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'es.config'`

- [ ] **Step 3: Write the implementation**

`es/es/config.py`:
```python
"""Config access for es. Reads the mounted /opt/config.yaml directly (no
envdir). Derived constants that configure.py used to inject live here."""
import os
from pathlib import Path

import yaml

# In-container Radicale CalDAV endpoint — a derived constant, not in config.yaml.
CALDAV_URL = "http://localhost:5232"


def _config_path() -> Path:
    return Path(os.environ.get("ES_CONFIG_PATH", "/opt/config.yaml"))


def load_config() -> dict:
    path = _config_path()
    if not path.is_file():
        raise FileNotFoundError(f"es: config not found at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"es: config at {path} is not a mapping")
    return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add es/es/config.py es/tests/test_config.py
git commit -m "feat(es): config loader reading /opt/config.yaml directly"
```

---

### Task 3: Typer app + `--pretty` root callback + `@envelope` error wrapping

**Files:**
- Create: `es/es/main.py`
- Create: `es/es/runner.py`
- Test: `es/tests/test_main.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_main.py`:
```python
import json
import typer
from typer.testing import CliRunner
from es.runner import envelope

runner = CliRunner()


def test_envelope_wraps_return_value():
    app = typer.Typer()

    @app.command()
    @envelope
    def hello(ctx: typer.Context):
        return {"msg": "hi"}

    res = runner.invoke(app, ["hello"])
    assert res.exit_code == 0
    assert json.loads(res.stdout) == {"ok": True, "data": {"msg": "hi"}}


def test_envelope_catches_exception_into_error():
    app = typer.Typer()

    @app.command()
    @envelope
    def boom(ctx: typer.Context):
        raise KeyError("missing")

    res = runner.invoke(app, ["boom"])
    assert res.exit_code == 1
    body = json.loads(res.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "KeyError"
    assert "missing" in body["error"]["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'es.runner'`

- [ ] **Step 3: Write the runner (envelope decorator) and main app**

`es/es/runner.py`:
```python
"""@envelope: wrap a Typer command so its return value becomes the success
envelope and any exception becomes the error envelope (never a traceback).
Reads --pretty from the root context (ctx.obj)."""
import functools

import typer

from es import output


def _pretty(ctx: typer.Context) -> bool:
    return bool(ctx.obj and ctx.obj.get("pretty"))


def envelope(fn):
    @functools.wraps(fn)
    def wrapper(ctx: typer.Context, *args, **kwargs):
        try:
            data = fn(ctx, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - CLI boundary: never leak a traceback
            raise typer.Exit(
                output.emit_error(type(e).__name__, str(e), _pretty(ctx))
            )
        raise typer.Exit(output.emit(data, _pretty(ctx)))

    return wrapper
```

`es/es/main.py`:
```python
"""es — EverStone agent tool-gateway CLI. Explicit sub-app registry."""
import typer

from es.capabilities import tasks

app = typer.Typer(no_args_is_help=True, add_completion=False, help="EverStone agent CLI")


@app.callback()
def _root(ctx: typer.Context,
          pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON.")):
    ctx.obj = {"pretty": pretty}


app.add_typer(tasks.app, name="tasks", help="CalDAV tasks.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

> NOTE: `main.py` imports `es.capabilities.tasks`, created in Task 5. Until then, `test_main.py` only exercises `es.runner.envelope` (no import of `main`), so it passes independently.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_main.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add es/es/runner.py es/es/main.py es/tests/test_main.py
git commit -m "feat(es): Typer app, --pretty callback, envelope error wrapping"
```

---

### Task 4: Add `delete_task` to the TasksClient library

**Files:**
- Modify: `everstone_tasks/everstone_tasks/client.py` (add method after `set_note_link`)
- Test: `everstone_tasks/tests/test_client_delete.py`

- [ ] **Step 1: Write the failing test**

`everstone_tasks/tests/test_client_delete.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd everstone_tasks && python -m pytest tests/test_client_delete.py -v`
Expected: FAIL — `AttributeError: 'TasksClient' object has no attribute 'delete_task'`

- [ ] **Step 3: Add the method**

Append to `everstone_tasks/everstone_tasks/client.py` (after `set_note_link`):
```python
    def delete_task(self, uid, list_name):
        todo = self._find(uid, list_name)
        todo.delete()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd everstone_tasks && python -m pytest tests/test_client_delete.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add everstone_tasks/everstone_tasks/client.py everstone_tasks/tests/test_client_delete.py
git commit -m "feat(tasks-lib): add delete_task to TasksClient"
```

---

### Task 5: `es tasks` sub-app (list / add / done / delete)

**Files:**
- Create: `es/es/capabilities/__init__.py`
- Create: `es/es/capabilities/tasks.py`
- Test: `es/tests/test_tasks.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_tasks.py`:
```python
import json
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
    fake_client.delete_task.assert_called_once_with("u1", "inbox")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'es.capabilities'`

- [ ] **Step 3: Write the sub-app**

`es/es/capabilities/__init__.py`:
```python
```
(empty file)

`es/es/capabilities/tasks.py`:
```python
"""es tasks — CalDAV tasks via the everstone_tasks TasksClient (in-process)."""
from datetime import datetime
from typing import Optional, Tuple

import typer

from es import config
from es.runner import envelope
from everstone_tasks.client import TasksClient
from everstone_tasks.deeplink import build_deeplink

app = typer.Typer(no_args_is_help=True)

# group_safe: this capability is the ONLY one allowed in group chats.
GROUP_SAFE = True
# config.yaml keys this capability reads:
CONFIG_KEYS = ("caldav.user", "caldav.password", "obsidian.vault_name")


def _client() -> Tuple[TasksClient, str]:
    """Build the TasksClient + return the obsidian vault name, from config.yaml."""
    cfg = config.load_config()
    caldav = cfg.get("caldav") or {}
    vault = (cfg.get("obsidian") or {}).get("vault_name", "")
    client = TasksClient(config.CALDAV_URL, caldav.get("user", ""), caldav.get("password", ""))
    return client, vault


@app.command("list")
@envelope
def list_tasks(ctx: typer.Context,
               list_name: str = typer.Option("inbox", "--list")):
    client, _ = _client()
    return client.list_tasks(list_name)


@app.command("add")
@envelope
def add_task(ctx: typer.Context,
            summary: str = typer.Argument(...),
            list_name: str = typer.Option("inbox", "--list"),
            note: Optional[str] = typer.Option(None, "--note"),
            remind_at: Optional[str] = typer.Option(None, "--remind-at")):
    client, vault = _client()
    url = build_deeplink(vault, note) if note else None
    remind = datetime.fromisoformat(remind_at) if remind_at else None
    uid = client.add_task(summary, list_name, url=url, remind_at=remind)
    return {"uid": uid}


@app.command("done")
@envelope
def done_task(ctx: typer.Context,
             uid: str = typer.Argument(...),
             list_name: str = typer.Option("inbox", "--list")):
    client, _ = _client()
    client.complete_task(uid, list_name)
    return {"uid": uid, "status": "COMPLETED"}


@app.command("delete")
@envelope
def delete_task(ctx: typer.Context,
               uid: str = typer.Argument(...),
               list_name: str = typer.Option("inbox", "--list")):
    client, _ = _client()
    client.delete_task(uid, list_name)
    return {"uid": uid, "deleted": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_tasks.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add es/es/capabilities/__init__.py es/es/capabilities/tasks.py es/tests/test_tasks.py
git commit -m "feat(es): tasks sub-app (list/add/done/delete) over TasksClient"
```

---

### Task 6: Full suite + editable install smoke check

**Files:**
- Test: (runs the whole `es` suite + a real CLI entrypoint smoke test)

- [ ] **Step 1: Install editable + deps**

Run:
```bash
cd es && pip install -e . && pip install -e ../everstone_tasks
```
Expected: installs `es` and `everstone-tasks`; the `es` console script is on PATH.

- [ ] **Step 2: Run the whole es suite**

Run: `cd es && python -m pytest -v`
Expected: PASS (all output/config/main/tasks tests green)

- [ ] **Step 3: Smoke-test the real entrypoint (error path needs no network)**

Run: `ES_CONFIG_PATH=/nonexistent es tasks list`
Expected: prints `{"ok": false, "error": {"code": "FileNotFoundError", "message": "es: config not found at /nonexistent"}}` and exits non-zero.

- [ ] **Step 4: Commit (nothing to change if green; otherwise fix + commit)**

```bash
git add -A es
git commit -m "test(es): full suite + entrypoint smoke" --allow-empty
```

---

## Self-Review

- **Spec coverage (Plan 1 slice):** ✅ es package + Typer registry (Task 1,3); config-from-config.yaml, no envdir (Task 2); JSON envelope + `--pretty` (Task 1,3); `es tasks` add/list/done/delete reusing TasksClient (Task 4,5); per-capability contract fields `GROUP_SAFE`/`CONFIG_KEYS` (Task 5). Out of Plan-1 scope (covered by Plans 2/3): `es cal`, Google auth, access_hook, Dockerfile/configure/services, AGENTS/skill, deprecating old binaries.
- **Placeholder scan:** No TBD/TODO; every code + test step has complete content.
- **Type consistency:** `_client()` returns `(TasksClient, vault_str)` in tasks.py and is monkeypatched with the same shape in tests; `envelope` decorator signature `(ctx, *args, **kwargs)` matches all command signatures (each takes `ctx` first); `emit`/`emit_error` return ints used as exit codes consistently.
- **Note for executor:** Typer command functions must keep `ctx: typer.Context` as the first param for `@envelope` to read `--pretty`.
