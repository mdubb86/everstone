# EverStone — Hermes Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn EverStone into a single self-hosted container hosting Obsidian notes (CouchDB/LiveSync), CalDAV tasks (Radicale), and git backups, with one Hermes agent reachable via Telegram — full power in your DM, strictly tasks-only in any group, enforced by a fail-closed `pre_tool_call` hook keyed on `chat_type`.

**Architecture:** One Alpine + s6 container supervises `caddy · couchdb · radicale · git/fcgiwrap · livesync-bridge (Deno) · engraph (Rust) · hermes`. `configure.py` templates every service config from one `config.yaml`. Notes reach the agent as plaintext via livesync-bridge + engraph (local search/edit MCP); tasks via an `everstone_tasks` MCP tool + CLI. A `pre_tool_call` hook reads the session key (`agent:main:{platform}:{chat_type}:{chat_id}`) and allows all tools in `private` chats, only `everstone_tasks` in groups.

**Tech Stack:** Python 3.11 (CLI/MCP/configure/hook, `caldav`, `icalendar`, `pytest`, `uv`), Radicale 3.x, CouchDB 3.5, vrtmrz/livesync-bridge (Deno), devwhodevs/engraph (Rust), NousResearch/hermes-agent, Caddy, s6-overlay, Docker, `just`.

**Spec:** `docs/superpowers/specs/2026-06-03-everstone-hermes-design.md`

---

## File Structure

**New:**
- `everstone_tasks/pyproject.toml`, `everstone_tasks/everstone_tasks/{__init__,deeplink,client,cli,mcp}.py`, `everstone_tasks/tests/{conftest,test_deeplink,test_client,test_cli}.py`
- `access_hook/everstone_access_hook.py` + `access_hook/tests/test_access_hook.py` — the `pre_tool_call` policy script
- `config/radicale/config`, `config/hermes/hooks.yaml.tmpl` (hook registration)
- `services/radicale/{run,type}`, `services/livesync-bridge/{run,type}`, `services/engraph/{run,type}`, `services/setup_hermes/{up,type}`, `services/hermes/{run,type}`, `services/setup_engraph/{up,type}`
- `scripts/setup_hermes`, `scripts/setup_engraph`
- `scripts/tests/test_configure.py`
- `Justfile`
- `e2e/pyproject.toml`, `e2e/conftest.py`, `e2e/test_*.py`, `e2e/docker-compose.notes.yml`
- `docs/BOOTSTRAP.md`

**Modified:** `scripts/configure.py`, `config/defaults.yaml`, `config/schema.json`, `scripts/entrypoint`, `Dockerfile`, `routing.md`, `building-blocks.md`

**Removed:** `radfire/`, `taskite/`, `radfire_data/`

---

## Phase 0 — Verification spike (DECISION GATE)

The access model rests on two facts about Hermes. Confirm them **before** building the hook, because a failure changes the design (fallback: separate tasks-only group bot).

### Task 0: Confirm the `pre_tool_call` payload carries the structured session key

**Files:** none (throwaway probe)

- [ ] **Step 1: Install Hermes in a scratch container and register a logging hook**

Run:
```bash
docker run --rm -it python:3.11-slim bash -lc '
  pip install hermes-agent >/dev/null 2>&1 || pip install --pre hermes-agent;
  mkdir -p /root/.hermes/agent-hooks;
  cat > /root/.hermes/agent-hooks/probe.sh <<"EOF"
#!/usr/bin/env bash
cat - >> /tmp/hook-payloads.jsonl
printf "{}\n"
EOF
  chmod +x /root/.hermes/agent-hooks/probe.sh;
  cat > /root/.hermes/config.yaml <<"EOF"
hooks:
  pre_tool_call:
    - matcher: "*"
      command: "/root/.hermes/agent-hooks/probe.sh"
EOF
  echo "ready"'
```
Expected: prints `ready` (adjust the install command to the current Hermes package name if it differs).

- [ ] **Step 2: Drive one tool call from a DM and one from a group, capture payloads**

Run a minimal CLI/gateway interaction that triggers a tool call in each chat type (or, if a live gateway is impractical in CI, run `hermes` CLI which uses a `private`-equivalent session). Inspect `/tmp/hook-payloads.jsonl`:
```bash
# in the container, after triggering tool calls:
jq -r '.session_id' /tmp/hook-payloads.jsonl | sort -u
```
Expected: **either** `session_id` values like `agent:main:telegram:private:123…` / `agent:main:telegram:group:-100…` (structured — PROCEED with chat_type gating) **or** opaque `sess_…` (FALLBACK).

- [ ] **Step 3: Record the outcome in the plan**

Edit this task's checkbox note with one line: `RESULT: structured key | opaque`. If **opaque**, before continuing: either (a) confirm a session-store lookup maps `session_id → chat_type` (read `~/.hermes` state), or (b) switch Phase 4/5 to the two-bot fallback (a second `hermes` profile/bot whose `hermes tools` excludes everything but `everstone_tasks`). The rest of the plan assumes the structured-key path; the fallback only changes Task 12 + the hermes service.

- [ ] **Step 4: Confirm the hook fires for the terminal tool specifically**

In the same probe, trigger a shell command and confirm a payload with `"tool_name":"terminal"` was logged.
Run: `grep -c '"tool_name":"terminal"' /tmp/hook-payloads.jsonl`
Expected: ≥ 1. (If terminal calls bypass the hook, the wall is unsound — escalate to the two-bot fallback.)

---

## Phase 1 — `everstone_tasks` core (TDD)

### Task 1: Scaffold the package

**Files:** Create `everstone_tasks/pyproject.toml`, `everstone_tasks/everstone_tasks/__init__.py`

- [ ] **Step 1: pyproject.toml**

```toml
[project]
name = "everstone-tasks"
version = "0.1.0"
description = "CalDAV task tool (CLI + MCP) for EverStone / Hermes"
requires-python = ">=3.11"
dependencies = ["caldav>=1.3", "icalendar>=5.0", "mcp>=1.0"]

[project.optional-dependencies]
test = ["pytest>=8", "radicale>=3.2", "requests>=2.31"]

[project.scripts]
everstone-tasks = "everstone_tasks.cli:main"
everstone-tasks-mcp = "everstone_tasks.mcp:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

`__init__.py`:
```python
"""everstone-tasks: CalDAV task tool for EverStone and Hermes (CLI + MCP)."""
__version__ = "0.1.0"
```

- [ ] **Step 2: Install in a venv**

Run: `cd everstone_tasks && python3 -m venv .venv && .venv/bin/pip install -e ".[test]"`
Expected: installs cleanly; `everstone-tasks` and `everstone-tasks-mcp` scripts created.

- [ ] **Step 3: Commit**
```bash
git add everstone_tasks/pyproject.toml everstone_tasks/everstone_tasks/__init__.py
git commit -m "feat(tasks): scaffold everstone-tasks package (CLI+MCP)"
```

---

### Task 2: Deeplink builder

**Files:** Create `everstone_tasks/everstone_tasks/deeplink.py`, `everstone_tasks/tests/test_deeplink.py`

- [ ] **Step 1: Failing test**
```python
from everstone_tasks.deeplink import build_deeplink

def test_simple():
    assert build_deeplink("everstone", "Inbox.md") == "obsidian://open?vault=everstone&file=Inbox.md"

def test_encoded():
    assert build_deeplink("My Vault", "Projects/Q4 Report.md") == \
        "obsidian://open?vault=My%20Vault&file=Projects%2FQ4%20Report.md"

def test_strip_leading_slash():
    assert build_deeplink("v", "/a.md") == "obsidian://open?vault=v&file=a.md"
```

- [ ] **Step 2: Run, expect fail** — `cd everstone_tasks && .venv/bin/pytest tests/test_deeplink.py -v` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement**
```python
"""Build obsidian:// deeplinks for tasks that back onto a vault note."""
from urllib.parse import quote

def build_deeplink(vault_name: str, note_path: str) -> str:
    note_path = note_path.lstrip("/")
    return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(note_path, safe='')}"
```

- [ ] **Step 4: Run, expect pass** (3 passed).
- [ ] **Step 5: Commit** — `git commit -am "feat(tasks): obsidian deeplink builder"`

---

### Task 3: Ephemeral Radicale fixture

**Files:** Create `everstone_tasks/tests/conftest.py`

- [ ] **Step 1: Write the fixture**
```python
import socket, subprocess, sys, time
import pytest, requests

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

@pytest.fixture
def radicale(tmp_path):
    storage = tmp_path / "collections"; storage.mkdir()
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "radicale", "--server-hosts", f"127.0.0.1:{port}",
         "--auth-type", "none", "--storage-filesystem-folder", str(storage),
         "--logging-level", "warning"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.request("OPTIONS", base + "/", timeout=0.5); break
        except requests.exceptions.ConnectionError:
            time.sleep(0.1)
    else:
        proc.terminate(); raise RuntimeError("Radicale did not start")
    yield base
    proc.terminate()
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired: proc.kill()
```

- [ ] **Step 2: Smoke the fixture** — add a temp test asserting `radicale.startswith("http://127.0.0.1:")`, run it, then delete it. If radicale CLI flags differ, run `.venv/bin/python -m radicale --help` and adjust.
- [ ] **Step 3: Commit** — `git add everstone_tasks/tests/conftest.py && git commit -m "test(tasks): ephemeral radicale fixture"`

---

### Task 4: `TasksClient` — add/list, complete, link, alarm

**Files:** Create `everstone_tasks/everstone_tasks/client.py`, `everstone_tasks/tests/test_client.py`

- [ ] **Step 1: Failing tests**
```python
import pytest
from datetime import datetime, timezone
from everstone_tasks.client import TasksClient

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
                          remind_at=datetime(2030,1,1,9,0,tzinfo=timezone.utc))
    assert client.list_tasks("inbox")[0]["has_alarm"] is True
```

- [ ] **Step 2: Run, expect fail** — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**
```python
"""Thin CalDAV task client on caldav + icalendar."""
import uuid
from datetime import datetime
from typing import Optional
import caldav
from icalendar import Todo, Alarm, Calendar as ICalendar

class TasksClient:
    def __init__(self, url, username="", password=""):
        self._dav = caldav.DAVClient(url=url, username=username or None, password=password or None)
        self._principal = self._dav.principal()

    def _calendar(self, list_name):
        for cal in self._principal.calendars():
            if (cal.name or cal.id) == list_name:
                return cal
        raise KeyError(f"task list not found: {list_name}")

    def ensure_list(self, list_name):
        try:
            return self._calendar(list_name)
        except KeyError:
            return self._principal.make_calendar(
                name=list_name, cal_id=list_name,
                supported_calendar_component_set=["VTODO"])

    def add_task(self, summary, list_name, url: Optional[str] = None,
                 remind_at: Optional[datetime] = None) -> str:
        cal = self.ensure_list(list_name); uid = uuid.uuid4().hex
        todo = Todo()
        todo.add("uid", uid); todo.add("summary", summary); todo.add("status", "NEEDS-ACTION")
        if url: todo.add("url", url)
        if remind_at:
            alarm = Alarm()
            alarm.add("action", "DISPLAY"); alarm.add("description", summary)
            alarm.add("trigger", remind_at)   # absolute trigger
            todo.add_component(alarm)
        ical = ICalendar(); ical.add("prodid", "-//everstone-tasks//EN"); ical.add("version", "2.0")
        ical.add_component(todo)
        cal.save_todo(ical=ical.to_ical().decode())
        return uid

    def list_tasks(self, list_name):
        out = []
        for todo in self._calendar(list_name).todos(include_completed=True):
            c = todo.icalendar_component
            out.append({
                "uid": str(c.get("uid", "")), "summary": str(c.get("summary", "")),
                "status": str(c.get("status", "NEEDS-ACTION")),
                "url": str(c["url"]) if "url" in c else None,
                "has_alarm": any(sc.name == "VALARM" for sc in todo.icalendar_instance.walk()
                                 if sc.name == "VALARM"),
            })
        return out

    def _find(self, uid, list_name):
        for todo in self._calendar(list_name).todos(include_completed=True):
            if str(todo.icalendar_component.get("uid", "")) == uid:
                return todo
        raise KeyError(uid)

    def complete_task(self, uid, list_name):
        todo = self._find(uid, list_name); c = todo.icalendar_component
        c["status"] = "COMPLETED"
        if "percent-complete" not in c: c.add("percent-complete", 100)
        todo.save()

    def set_note_link(self, uid, list_name, url):
        todo = self._find(uid, list_name); c = todo.icalendar_component
        if "url" in c: del c["url"]
        c.add("url", url); todo.save()
```

- [ ] **Step 4: Run, expect pass** (5 passed). If `has_alarm` walk logic errors on this caldav version, simplify to checking `b"BEGIN:VALARM" in todo.data.encode()`; re-run.
- [ ] **Step 5: Commit** — `git commit -am "feat(tasks): TasksClient add/list/complete/link + VALARM"`

---

### Task 5: CLI

**Files:** Create `everstone_tasks/everstone_tasks/cli.py`, `everstone_tasks/tests/test_cli.py`

Env: `EVERSTONE_CALDAV_URL/USER/PASSWORD`, `EVERSTONE_VAULT_NAME`.

- [ ] **Step 1: Failing test**
```python
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
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**
```python
"""CLI for everstone-tasks."""
import argparse, json, os, sys
from datetime import datetime
from typing import Optional
from .client import TasksClient
from .deeplink import build_deeplink

def _client(env):
    url = env.get("EVERSTONE_CALDAV_URL")
    if not url: raise SystemExit("EVERSTONE_CALDAV_URL not set")
    return TasksClient(url, env.get("EVERSTONE_CALDAV_USER", ""), env.get("EVERSTONE_CALDAV_PASSWORD", ""))

def _parser():
    p = argparse.ArgumentParser(prog="everstone-tasks")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list"); pl.add_argument("--list", dest="list_name", default="inbox")
    pa = sub.add_parser("add"); pa.add_argument("summary")
    pa.add_argument("--list", dest="list_name", default="inbox")
    pa.add_argument("--note", default=None); pa.add_argument("--remind-at", dest="remind_at", default=None)
    pd = sub.add_parser("done"); pd.add_argument("uid"); pd.add_argument("--list", dest="list_name", default="inbox")
    pk = sub.add_parser("link"); pk.add_argument("uid"); pk.add_argument("--note", required=True)
    pk.add_argument("--list", dest="list_name", default="inbox")
    return p

def main(argv: Optional[list] = None, env: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    a = _parser().parse_args(argv); c = _client(env)
    if a.cmd == "list":
        ts = c.list_tasks(a.list_name)
        print(json.dumps(ts) if a.json else "\n".join(
            f"[{'x' if t['status']=='COMPLETED' else ' '}] {t['summary']} ({t['uid']})" for t in ts))
        return 0
    if a.cmd == "add":
        url = build_deeplink(env["EVERSTONE_VAULT_NAME"], a.note) if a.note else None
        remind = datetime.fromisoformat(a.remind_at) if a.remind_at else None
        uid = c.add_task(a.summary, a.list_name, url=url, remind_at=remind)
        print(json.dumps({"uid": uid}) if a.json else uid); return 0
    if a.cmd == "done":
        c.complete_task(a.uid, a.list_name)
        if a.json: print(json.dumps({"uid": a.uid, "status": "COMPLETED"}))
        return 0
    if a.cmd == "link":
        url = build_deeplink(env["EVERSTONE_VAULT_NAME"], a.note)
        c.set_note_link(a.uid, a.list_name, url)
        if a.json: print(json.dumps({"uid": a.uid, "url": url}))
        return 0
    return 1
```

- [ ] **Step 4: Run full suite** — `cd everstone_tasks && .venv/bin/pytest -v` → all pass.
- [ ] **Step 5: Commit** — `git commit -am "feat(tasks): CLI add/list/done/link with deeplink + remind-at"`

---

### Task 6: MCP server wrapping the same logic

**Files:** Create `everstone_tasks/everstone_tasks/mcp.py`

The agent calls `everstone_tasks` as a discrete MCP tool (this is what the access hook allowlists in groups — no shell needed). It reuses `TasksClient`.

- [ ] **Step 1: Implement the MCP server**
```python
"""MCP server exposing everstone_tasks as discrete tools (stdio)."""
import os
from datetime import datetime
from typing import Optional
from mcp.server.fastmcp import FastMCP
from .client import TasksClient
from .deeplink import build_deeplink

mcp = FastMCP("everstone_tasks")

def _client():
    return TasksClient(os.environ["EVERSTONE_CALDAV_URL"],
                       os.environ.get("EVERSTONE_CALDAV_USER", ""),
                       os.environ.get("EVERSTONE_CALDAV_PASSWORD", ""))

@mcp.tool()
def list_tasks(list_name: str = "inbox") -> list:
    """List tasks in a list."""
    return _client().list_tasks(list_name)

@mcp.tool()
def add_task(summary: str, list_name: str = "inbox",
             note_path: Optional[str] = None, remind_at: Optional[str] = None) -> dict:
    """Add a task. note_path stamps an obsidian deeplink; remind_at (ISO 8601) persists a VALARM."""
    url = build_deeplink(os.environ["EVERSTONE_VAULT_NAME"], note_path) if note_path else None
    remind = datetime.fromisoformat(remind_at) if remind_at else None
    return {"uid": _client().add_task(summary, list_name, url=url, remind_at=remind)}

@mcp.tool()
def complete_task(uid: str, list_name: str = "inbox") -> dict:
    """Mark a task complete."""
    _client().complete_task(uid, list_name); return {"uid": uid, "status": "COMPLETED"}

@mcp.tool()
def link_task(uid: str, note_path: str, list_name: str = "inbox") -> dict:
    """Set the obsidian deeplink on a task."""
    url = build_deeplink(os.environ["EVERSTONE_VAULT_NAME"], note_path)
    _client().set_note_link(uid, list_name, url); return {"uid": uid, "url": url}

def run():
    mcp.run()
```

- [ ] **Step 2: Smoke it starts** — `cd everstone_tasks && EVERSTONE_CALDAV_URL=http://x .venv/bin/python -c "import everstone_tasks.mcp"` → imports without error. (If `mcp` API differs by version, adjust `FastMCP` import per the installed `mcp` package; confirm with `.venv/bin/python -c "import mcp; print(mcp.__version__)"`.)
- [ ] **Step 3: Commit** — `git commit -am "feat(tasks): everstone_tasks MCP server (stdio)"`

---

## Phase 2 — The access hook (TDD)

### Task 7: `pre_tool_call` policy script

**Files:** Create `access_hook/everstone_access_hook.py`, `access_hook/tests/test_access_hook.py`

Policy: parse `session_id` of form `agent:main:{platform}:{chat_type}:{chat_id}`; `private` → allow all; `group`/`supergroup` → allow only the tasks tool; unparseable → deny. Allowed-in-group tool names come from env `EVERSTONE_GROUP_TOOLS` (comma-sep), default `everstone_tasks`.

- [ ] **Step 1: Failing tests**
```python
import json, subprocess, sys, os
from pathlib import Path
HOOK = Path(__file__).resolve().parents[1] / "everstone_access_hook.py"

def run_hook(payload, env=None):
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env={**os.environ, **(env or {})})
    return json.loads(p.stdout or "{}")

def test_dm_allows_terminal():
    out = run_hook({"tool_name": "terminal", "session_id": "agent:main:telegram:private:111"})
    assert out == {}  # allow

def test_group_blocks_terminal():
    out = run_hook({"tool_name": "terminal", "session_id": "agent:main:telegram:group:-100"})
    assert out.get("decision") == "block"

def test_group_allows_tasks():
    out = run_hook({"tool_name": "everstone_tasks", "session_id": "agent:main:telegram:supergroup:-100"})
    assert out == {}

def test_unparseable_denies_notes():
    out = run_hook({"tool_name": "terminal", "session_id": "sess_opaque"})
    assert out.get("decision") == "block"

def test_unparseable_allows_tasks_only_if_configured_strict():
    # default: opaque/unknown => fail closed => only tasks allowed
    assert run_hook({"tool_name": "everstone_tasks", "session_id": "sess_opaque"}) == {}
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**
```python
#!/usr/bin/env python3
"""pre_tool_call access hook: allow all tools in private chats; tasks-only elsewhere.

Reads the JSON payload on stdin, prints a JSON decision on stdout.
Fail-closed: any chat that is not clearly the owner's private DM is tasks-only.
"""
import json, os, sys

def allowed_group_tools():
    return set(t.strip() for t in os.environ.get("EVERSTONE_GROUP_TOOLS", "everstone_tasks").split(",") if t.strip())

def chat_type_of(session_id: str):
    # expected: agent:main:{platform}:{chat_type}:{chat_id}
    parts = session_id.split(":") if session_id else []
    if len(parts) >= 5 and parts[0] == "agent":
        return parts[3]
    return None

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"decision": "block", "reason": "unreadable hook payload"})); return
    tool = payload.get("tool_name", "")
    ctype = chat_type_of(payload.get("session_id", ""))
    if ctype == "private":
        print("{}"); return                      # owner DM → allow everything
    # group / supergroup / channel / unknown / unparseable → tasks-only (fail-closed)
    if tool in allowed_group_tools():
        print("{}"); return
    print(json.dumps({"decision": "block",
                      "reason": f"'{tool}' not permitted outside a private chat"}))

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect pass** (5 passed).
- [ ] **Step 5: Commit** — `git add access_hook && git commit -m "feat(hook): fail-closed pre_tool_call chat-type access policy"`

> If Phase 0 found an **opaque** session_id: replace `chat_type_of` to look up the chat from Hermes's session store keyed by `session_id`, OR adopt the two-bot fallback and drop this hook for the group bot. Either way, keep the tests.

---

## Phase 3 — Config generation (`configure.py`)

### Task 8: Defaults + schema

**Files:** Modify `config/defaults.yaml`, `config/schema.json`

- [ ] **Step 1: `config/defaults.yaml`**
```yaml
couchdb: {user: everstone, password: null, database: everstone}
git: {user: everstone, password: null}
caldav: {user: everstone, password: null}
livesync: {passphrase: null, obfuscate_passphrase: null}
obsidian: {vault_name: everstone}
instance: {name: Jarvis}
telegram: {owner_user_id: null, bot_token: null}
hermes: {model: null}
```

- [ ] **Step 2: `config/schema.json`** — require all sections; `telegram.owner_user_id` integer, `telegram.bot_token` non-empty string; passwords/passphrases non-empty strings; `hermes.model`, `instance.name`, `obsidian.vault_name` non-empty strings. (Mirror the draft-07 structure already in the repo, adding the new sections.)

- [ ] **Step 3: Commit** — `git commit -am "feat(config): config schema for v2 (telegram owner id, livesync, instance, hermes)"`

---

### Task 9: Make configure.py importable + path roots overridable

**Files:** Modify `scripts/configure.py`; Create `scripts/tests/test_configure.py`

- [ ] **Step 1: Test that import has no `/opt` side effects + deep_merge works** (as in repo's current functions).
```python
import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("configure", ROOT/"scripts"/"configure.py")
configure = importlib.util.module_from_spec(spec); spec.loader.exec_module(configure)
SAMPLE = {
  "couchdb": {"user":"u","password":"p","database":"vault"},
  "git": {"user":"g","password":"gp"}, "caldav": {"user":"cu","password":"cp"},
  "livesync": {"passphrase":"ph","obfuscate_passphrase":"ob"},
  "obsidian": {"vault_name":"myvault"}, "instance": {"name":"Jarvis"},
  "telegram": {"owner_user_id":111,"bot_token":"TKN"}, "hermes": {"model":"openai/gpt-5-codex"},
}
def test_deep_merge():
    assert configure.deep_merge({"a":{"x":1,"y":2}}, {"a":{"y":9}}) == {"a":{"x":1,"y":9}}
```

- [ ] **Step 2: Run** → fails on `/opt` import or assertion.
- [ ] **Step 3: Make `DEFAULTS_CONFIG_DIR`/`CONFIG_DIR`/`DATA_DIR` env-overridable** (via `os.environ.get(..., "/opt/...")`); ensure no import-time work outside the `__main__` guard.
- [ ] **Step 4: Run** → pass.
- [ ] **Step 5: Commit** — `git commit -am "refactor(config): overridable path roots + import-safe"`

---

### Task 10: Generators — radicale, livesync-bridge, hermes env, access-hook policy

**Files:** Modify `scripts/configure.py`; Create `config/radicale/config`; extend `scripts/tests/test_configure.py`

- [ ] **Step 1: `config/radicale/config`**
```ini
[server]
hosts = 127.0.0.1:5232
[auth]
type = htpasswd
htpasswd_filename = /opt/data/radicale/htpasswd
htpasswd_encryption = plain
[storage]
filesystem_folder = /opt/data/radicale/collections
[logging]
level = info
```

- [ ] **Step 2: Failing tests** for `generate_radicale_config`, `generate_livesync_bridge_config`, `generate_hermes_env` (assert: radicale htpasswd `cu:cp`; bridge JSON has couchdb peer with `database=vault`, `passphrase=ph`, storage peer `baseDir=/opt/data/vault/`, same `group`; hermes envdir files `EVERSTONE_CALDAV_URL/USER/PASSWORD`, `EVERSTONE_VAULT_NAME`, `EVERSTONE_AGENT_NAME=Jarvis`, `HERMES_MODEL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_USER_ID=111`, `EVERSTONE_GROUP_TOOLS=everstone_tasks`).

- [ ] **Step 3: Implement the generators** (each writes under `CONFIG_DIR`; bridge config mirrors Task-10 schema from the spec; hermes env writes a sourceable file + an s6 `envdir/`). Add `vault/`, `hermes/`, `radicale/` dir creation to `setup_data_directories`. Register all generator calls in `main()`.

- [ ] **Step 4: Run** → all pass.
- [ ] **Step 5: Commit** — `git commit -am "feat(config): generate radicale/bridge/hermes-env from config.yaml"`

---

## Phase 4 — Services & removals

### Task 11: Remove radfire and taskite

- [ ] **Step 1:** `git rm -r radfire taskite && rm -rf radfire_data`
- [ ] **Step 2:** `grep -rn radfire . --exclude-dir=.git --exclude-dir=docs` → only Dockerfile (Task 14) / docs remain.
- [ ] **Step 3:** `git commit -am "chore: remove radfire and taskite"`

### Task 12: Radicale, livesync-bridge, hermes, engraph services

**Files:** Create the `services/*/run` + `type` files and `scripts/setup_hermes`, `scripts/setup_engraph`.

- [ ] **Step 1: Radicale** (`type=longrun`):
```
#!/command/execlineb -P
with-contenv
radicale --config /opt/config/radicale/config
```

- [ ] **Step 2: livesync-bridge** (`type=longrun`):
```
#!/command/execlineb -P
with-contenv
cd /opt/livesync-bridge
deno task run
```

- [ ] **Step 3: setup_hermes oneshot** (`scripts/setup_hermes`) — applies config via env file:
```sh
#!/bin/sh
set -eu
export HERMES_HOME=/opt/data/hermes
. /opt/config/hermes/env
hermes config set model "$HERMES_MODEL"
hermes config set terminal.backend local
hermes config set TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
hermes config set messaging.telegram.allowed_users "$TELEGRAM_OWNER_USER_ID"
hermes config set messaging.telegram.unknown_user_action ignore
hermes config set messaging.telegram.group_trigger mentions_only
# register the pre_tool_call hook (idempotent merge into config.yaml)
python3 /scripts/merge_hermes_hooks.py
echo "[setup_hermes] applied. Run 'hermes auth add codex-oauth' once (see docs/BOOTSTRAP.md)."
```
(Confirm exact `hermes config set` keys against `hermes config set --help` during Task 14; adjust if the messaging keys differ. `merge_hermes_hooks.py` writes the `hooks.pre_tool_call` entry pointing at `/opt/access_hook/everstone_access_hook.py` with `matcher: "*"`.)

- [ ] **Step 4: hermes longrun** (`type=longrun`):
```
#!/command/execlineb -P
with-contenv
s6-envdir -fn /opt/config/hermes/envdir
export HERMES_HOME /opt/data/hermes
hermes gateway run
```

- [ ] **Step 5: engraph index+serve** (Task 13 builds the binary). `setup_engraph` oneshot runs `engraph index /opt/data/vault` (idempotent); the longrun runs `engraph serve` — but engraph is consumed by Hermes as a stdio MCP, so instead register it in Hermes config as an MCP server (`merge_hermes_hooks.py` also adds the engraph + everstone_tasks MCP entries). Keep a `setup_engraph` oneshot that builds the initial index; re-index on a cron or `engraph serve`'s own watch.

- [ ] **Step 6:** `chmod +x` all `run`/`up`/script files; commit — `git commit -am "feat(services): radicale, livesync-bridge, hermes (+setup), engraph index"`

---

## Phase 5 — Image build

### Task 13: Dockerfile

**Files:** Modify `Dockerfile`, `scripts/entrypoint`

- [ ] **Step 1:** Remove the radfire `COPY`/`pip install` lines.
- [ ] **Step 2:** Final stage `apk add`: add `deno nodejs npm rust cargo` (rust/cargo for engraph musl build; if too heavy, build engraph in a dedicated builder stage). Keep python3/py3-pip.
- [ ] **Step 3:** `RUN pip install --break-system-packages "radicale>=3.2"`; `COPY everstone_tasks /opt/everstone_tasks && pip install --break-system-packages /opt/everstone_tasks`; `COPY access_hook /opt/access_hook`.
- [ ] **Step 4:** `RUN pip install --break-system-packages hermes-agent && hermes --help >/dev/null`.
- [ ] **Step 5:** Vendor bridge: `RUN git clone --depth 1 https://github.com/vrtmrz/livesync-bridge /opt/livesync-bridge`.
- [ ] **Step 6:** Build engraph (musl): `RUN cargo install --git https://github.com/devwhodevs/engraph --root /usr/local` (verify the binary name; if prebuilt glibc binaries are needed, add `gcompat`). The ~300MB GGUF model downloads on first `engraph index` to the volume.
- [ ] **Step 7:** `scripts/entrypoint`: after `configure.py`, symlink `ln -sf /opt/config/livesync-bridge/config.json /opt/livesync-bridge/dat/config.json`.
- [ ] **Step 8:** `docker build -t everstone:dev .` — resolve runtime specifics inline (deno apk vs installer; engraph crate/binary name; `hermes config set` key names; bridge `deno task` name via `cat /opt/livesync-bridge/deno.json`).
- [ ] **Step 9:** Commit — `git commit -am "build: deno+node+rust+hermes+radicale+tasks+engraph; vendor bridge; drop radfire"`

---

## Phase 6 — `just` + `uv` e2e battery

### Task 14: Justfile + e2e uv project skeleton

**Files:** Create `Justfile`, `e2e/pyproject.toml`, `e2e/conftest.py`

- [ ] **Step 1: `Justfile`**
```make
set shell := ["bash", "-cu"]
IMAGE := "everstone:dev"
NAME := "everstone-e2e"

build:
    docker build -t {{IMAGE}} .

up: build
    e2e/.venv/bin/python e2e/up.py            # writes config.yaml, runs container as {{NAME}} on a free port

test: 
    cd e2e && uv run pytest -v

down:
    docker rm -f {{NAME}} 2>/dev/null || true

e2e: build
    cd e2e && uv run pytest -v
```

- [ ] **Step 2: `e2e/pyproject.toml`** — deps `pytest`, `requests`, `caldav`, `icalendar`; a `conftest.py` fixture that builds+boots the container under `everstone-e2e` with a temp `/opt/data` + a generated `config.yaml`, waits for `/health`, yields `{base_url, container_name, data_dir}`, and tears down (`docker rm -f`).
- [ ] **Step 3:** `cd e2e && uv venv && uv pip install -e .` (or `uv sync`); commit — `git commit -am "test(e2e): just + uv harness skeleton"`

---

### Task 15: E2E — routing, liveness, tasks/notes round-trips

**Files:** Create `e2e/test_routing.py`, `e2e/test_tasks.py`, `e2e/test_notes.py`, `e2e/docker-compose.notes.yml`

- [ ] **Step 1: routing/liveness** — assert `/health`==200; `/caldav/` returns 401/207; each s6 service is `up` via `docker exec {name} s6-rc -a list` (or check listening ports).
- [ ] **Step 2: tasks round-trip + deeplink resolves** — via `caldav` against the published `/caldav`, add/list/done/link; assert deeplink decodes to an existing file under `/opt/data/vault` (write a fixture note first).
- [ ] **Step 3: notes round-trip** — `e2e/docker-compose.notes.yml` runs CouchDB + two `everstone:dev` containers as bridges (configs from the spec's two-bridge harness); assert a file written in vault A appears in vault B. Gate behind `RUN_NOTES_E2E=1`.
- [ ] **Step 4:** Run `just e2e` (+ the gated notes test); commit — `git commit -am "test(e2e): routing, liveness, tasks+deeplink, notes round-trip"`

---

### Task 16: E2E — the access-control battery (headline)

**Files:** Create `e2e/test_access_control.py`

- [ ] **Step 1: Verify the hook via `docker exec`, simulating both chat types.** The hook is a pure stdin→stdout script, so the test pipes payloads to it inside the container and asserts decisions — this proves the *policy* on the real image:
```python
import json, subprocess
def _hook(container, payload):
    p = subprocess.run(["docker","exec","-i",container,"python3","/opt/access_hook/everstone_access_hook.py"],
                       input=json.dumps(payload), capture_output=True, text=True)
    return json.loads(p.stdout or "{}")

def test_group_blocks_shell_and_notes(everstone):
    for tool in ["terminal","read_file","engraph","spawn_subagent"]:
        out = _hook(everstone["container_name"],
                    {"tool_name": tool, "session_id":"agent:main:telegram:group:-100"})
        assert out.get("decision")=="block", tool

def test_group_allows_tasks(everstone):
    assert _hook(everstone["container_name"],
                 {"tool_name":"everstone_tasks","session_id":"agent:main:telegram:supergroup:-100"})=={}

def test_dm_allows_all(everstone):
    for tool in ["terminal","engraph","everstone_tasks"]:
        assert _hook(everstone["container_name"],
                     {"tool_name":tool,"session_id":"agent:main:telegram:private:111"})=={}

def test_opaque_session_fails_closed(everstone):
    assert _hook(everstone["container_name"],
                 {"tool_name":"terminal","session_id":"sess_opaque"}).get("decision")=="block"
```

- [ ] **Step 2: Gateway-lockdown config assertion** — `docker exec` reads the generated Hermes config/envdir; assert `TELEGRAM_OWNER_USER_ID` set, `unknown_user_action=ignore`, no allow-all, and the `pre_tool_call` hook is registered pointing at the access hook.
- [ ] **Step 3:** Run; commit — `git commit -am "test(e2e): access-control (group=tasks-only, DM=all, fail-closed) + lockdown config"`

---

### Task 17: E2E — persistence & backup/restore

**Files:** Create `e2e/test_persistence.py`

- [ ] **Step 1:** Restart the container (`docker restart`); assert tasks + a vault file + `/opt/data/hermes` token file survive.
- [ ] **Step 2:** Tar `/opt/data`, restore into a fresh container, assert tasks + notes survive.
- [ ] **Step 3:** Commit — `git commit -am "test(e2e): persistence + backup/restore of /opt/data"`

---

## Phase 7 — Docs

### Task 18: Bootstrap runbook + routing/building-blocks updates

**Files:** Create `docs/BOOTSTRAP.md`; Modify `routing.md`, `building-blocks.md`

- [ ] **Step 1: `docs/BOOTSTRAP.md`** — config.yaml template (owner_user_id only; no group ids), `run_local.sh`, one-time `hermes auth add codex-oauth`, leave Telegram **privacy mode ON**, `EVERSTONE_GROUP_TOOLS`, point clients (Obsidian `setupuri`, Tasks.org / Mac at `/caldav`, Tailscale note), and the **external lockdown check** (have a friend DM the bot; confirm no agent activity in logs).
- [ ] **Step 2:** `routing.md`: note radfire removed, `/caldav` is stock Radicale, link the spec. `building-blocks.md`: add a SUPERSEDED banner.
- [ ] **Step 3:** Commit — `git commit -am "docs: bootstrap runbook; mark building-blocks superseded"`

---

## Final verification

- [ ] **Unit:** `everstone_tasks/.venv/bin/pytest everstone_tasks -v` ; `python3 -m pytest scripts/tests access_hook/tests -v` → all pass.
- [ ] **Build + e2e:** `just e2e` → routing, liveness, tasks/deeplink, access-control, lockdown, persistence, backup all pass; `RUN_NOTES_E2E=1` notes round-trip passes.
- [ ] **Manual smoke (documented):** real Obsidian device against a throwaway vault; confirm a note edit round-trips and a Hermes-written file appears back; confirm in your DM the agent uses notes and in the group it refuses everything but tasks.
