# es tasks Subtasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one level of parent/child **subtasks** to `es tasks` via CalDAV `RELATED-TO;RELTYPE=PARENT`, created on explicit request only.

**Architecture:** Extend `TasksClient` (in `es/es/tasks_client.py`) to write/read `RELATED-TO`, resolve a task's list by uid, enumerate children, and cascade-delete behind a guard. Surface it through `es tasks` verbs (`add --parent`, `edit --parent`/detach, `parent` in `list`, `delete --force`). Behavior (on-request, one-level, confirm-before-force) lives in the `todos`/`checklists` skills; `AGENTS.md` gets a `--parent` example for discoverability.

**Tech Stack:** Python 3.12 (uv), Typer, `caldav` + `icalendar`, Radicale (test fixture), pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-es-subtasks-design.md` (D1 on-request, D2 one level, D3 independent completion, D4 delete guarded-unless-`--force`, D5 flat+`parent` output, D6 skills `todos`+`checklists`).

---

## Pre-flight

- [ ] **Confirm branch.** Run `git branch --show-current`. Expected: `feat/hermes-hub`. Implement on this branch (the `es tasks` capability + recent fixes live here). Do NOT implement on `master`. If somehow on `master`, run `git checkout -b feat/es-subtasks` first.
- [ ] **Confirm tests run.** Run `cd es && uv run pytest -q`. Expected: existing suite passes (≈59 tests). This establishes the green baseline.

## File Structure

- `es/es/tasks_client.py` — **Modify.** Add `ParentNotFound`/`HasSubtasks` exceptions; `_read_parent`; `_find_in_any_list`; `children_of`; `parent_uid` on `add_task`/`edit_task`; `parent` in `list_tasks`; `force` + cascade in `delete_task`.
- `es/tests/test_tasks_client.py` — **Modify.** Radicale round-trip tests for the above.
- `es/es/capabilities/tasks.py` — **Modify.** `--parent` on `add`/`edit`; `--force` on `delete`. (`list` passes the new `parent` field through unchanged.)
- `es/tests/test_tasks.py` — **Modify.** Mock-client tests for the new flags + error envelopes.
- `scripts/configure.py` — **Modify.** Add a `--parent` example to the `AGENTS.md` task section (`_AGENTS_PLATFORM_TEMPLATE`).
- `scripts/tests/test_configure.py` — **Run** (keep green; additive).
- `.devm/.everstone/hermes/profiles/everstone/skills/todos/SKILL.md` — **Modify** (profile dir; gitignored, not committed). Add a "Subtasks" section.
- `.devm/.everstone/hermes/profiles/everstone/skills/checklists/SKILL.md` — **Modify** (profile dir). Brief subtask note.

---

### Task 1: TasksClient — exceptions, read parent, find-by-uid, list_tasks parent field

**Files:**
- Modify: `es/es/tasks_client.py`
- Test: `es/tests/test_tasks_client.py`

- [ ] **Step 1: Write the failing tests**

Add to `es/tests/test_tasks_client.py`:

```python
from es.tasks_client import TasksClient, ParentNotFound, HasSubtasks


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd es && uv run pytest tests/test_tasks_client.py::test_list_tasks_parent_none_by_default tests/test_tasks_client.py::test_find_in_any_list_locates_uid tests/test_tasks_client.py::test_find_in_any_list_raises_when_missing -v`
Expected: FAIL — `ImportError` for `ParentNotFound`/`HasSubtasks`, and `parent`/`_find_in_any_list` not defined.

- [ ] **Step 3: Implement**

In `es/es/tasks_client.py`, after the imports (after line 5 `from icalendar import ...`), add the exception classes:

```python


class ParentNotFound(Exception):
    """Raised when a --parent uid is not found in any list."""
    es_code = "parent_not_found"


class HasSubtasks(Exception):
    """Raised when deleting a task with subtasks without force."""
    es_code = "has_subtasks"
```

Add two helpers inside `TasksClient` (place after the existing `_read_due` staticmethod):

```python
    @staticmethod
    def _read_parent(c):
        if "related-to" not in c:
            return None
        rel = c.get("related-to")
        items = rel if isinstance(rel, list) else [rel]
        for entry in items:
            params = getattr(entry, "params", {}) or {}
            reltype = str(params.get("RELTYPE", "PARENT")).upper()
            if reltype == "PARENT":
                return str(entry)
        return None

    def _find_in_any_list(self, uid):
        for cal in self._principal.calendars():
            name = cal.get_display_name() if hasattr(cal, "get_display_name") else cal.name
            name = name or cal.id
            for todo in cal.todos(include_completed=True):
                if str(todo.icalendar_component.get("uid", "")) == uid:
                    return todo, name
        raise ParentNotFound(f"parent task not found: {uid}")
```

In `list_tasks`, add the `parent` field to the appended dict (in the `out.append({...})` call, add the key):

```python
                "due": self._read_due(c),
                "parent": self._read_parent(c),
            })
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd es && uv run pytest tests/test_tasks_client.py -v`
Expected: PASS (new tests + existing client tests).

- [ ] **Step 5: Commit**

```bash
git add es/es/tasks_client.py es/tests/test_tasks_client.py
git commit -m "feat(es-tasks): TasksClient read parent (RELATED-TO) + find-by-uid"
```

---

### Task 2: TasksClient — add_task creates child in parent's list

**Files:**
- Modify: `es/es/tasks_client.py`
- Test: `es/tests/test_tasks_client.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd es && uv run pytest tests/test_tasks_client.py::test_add_with_parent_sets_related_to_in_parent_list tests/test_tasks_client.py::test_add_with_unknown_parent_raises -v`
Expected: FAIL — `add_task() got an unexpected keyword argument 'parent_uid'`.

- [ ] **Step 3: Implement**

In `es/es/tasks_client.py`, change the `add_task` signature to accept `parent_uid` and place the child in the parent's list, setting `RELATED-TO`. Replace the start of `add_task`:

```python
    def add_task(self, summary, list_name, url: Optional[str] = None,
                 remind_at: Optional[datetime] = None,
                 due: Optional[datetime] = None,
                 tags: Optional[list] = None,
                 parent_uid: Optional[str] = None) -> str:
        if parent_uid:
            _, list_name = self._find_in_any_list(parent_uid)  # child shares parent's list
        cal = self.ensure_list(list_name); uid = uuid.uuid4().hex
        todo = Todo()
        todo.add("uid", uid); todo.add("summary", summary); todo.add("status", "NEEDS-ACTION")
        if parent_uid:
            todo.add("related-to", parent_uid, parameters={"RELTYPE": "PARENT"})
        if url:
            todo.add("url", url)
```

(Leave the rest of `add_task` — `due`/`tags`/`remind_at`/save/return — unchanged.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd es && uv run pytest tests/test_tasks_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add es/es/tasks_client.py es/tests/test_tasks_client.py
git commit -m "feat(es-tasks): add_task --parent creates child in parent's list"
```

---

### Task 3: TasksClient — edit_task re-parent + detach

**Files:**
- Modify: `es/es/tasks_client.py`
- Test: `es/tests/test_tasks_client.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd es && uv run pytest tests/test_tasks_client.py::test_edit_set_parent tests/test_tasks_client.py::test_edit_detach_parent tests/test_tasks_client.py::test_edit_parent_none_leaves_link_untouched -v`
Expected: FAIL — `edit_task() got an unexpected keyword argument 'parent_uid'`.

- [ ] **Step 3: Implement**

In `es/es/tasks_client.py`, change `edit_task` to accept `parent_uid` and set/detach `RELATED-TO`. Update the signature and add the parent handling before `todo.save()`:

```python
    def edit_task(self, uid, list_name, summary: Optional[str] = None,
                  due: Optional[datetime] = None,
                  remind_at: Optional[datetime] = None,
                  tags: Optional[list] = None,
                  parent_uid: Optional[str] = None) -> None:
        todo = self._find(uid, list_name); c = todo.icalendar_component
        if summary is not None:
            c["summary"] = summary
```

(Keep the existing `due`/`tags`/`remind_at` blocks unchanged.) Then, immediately before the final `todo.save()`, add:

```python
        if parent_uid is not None:  # None = leave untouched; "" = detach; uid = set
            if "related-to" in c:
                del c["related-to"]
            if parent_uid != "":
                c.add("related-to", parent_uid, parameters={"RELTYPE": "PARENT"})
        todo.save()
```

(Remove the old standalone `todo.save()` so there is exactly one save at the end.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd es && uv run pytest tests/test_tasks_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add es/es/tasks_client.py es/tests/test_tasks_client.py
git commit -m "feat(es-tasks): edit_task --parent re-parent + detach"
```

---

### Task 4: TasksClient — children_of + guarded cascade delete

**Files:**
- Modify: `es/es/tasks_client.py`
- Test: `es/tests/test_tasks_client.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_children_of_lists_subtasks(client):
    parent = client.add_task("Parent", list_name="inbox")
    c1 = client.add_task("c1", list_name="inbox", parent_uid=parent)
    client.add_task("c2", list_name="inbox", parent_uid=parent)
    client.add_task("unrelated", list_name="inbox")
    kids = client.children_of(parent, "inbox")
    assert len(kids) == 2


def test_delete_parent_without_force_raises(client):
    parent = client.add_task("Parent", list_name="inbox")
    client.add_task("kid", list_name="inbox", parent_uid=parent)
    with pytest.raises(HasSubtasks):
        client.delete_task(parent, "inbox")


def test_delete_parent_force_removes_children(client):
    parent = client.add_task("Parent", list_name="inbox")
    client.add_task("kid", list_name="inbox", parent_uid=parent)
    client.delete_task(parent, "inbox", force=True)
    assert client.list_tasks("inbox") == []


def test_delete_leaf_needs_no_force(client):
    uid = client.add_task("Leaf", list_name="inbox")
    client.delete_task(uid, "inbox")
    assert client.list_tasks("inbox") == []


def test_done_parent_leaves_children_open(client):
    parent = client.add_task("Parent", list_name="inbox")
    child = client.add_task("kid", list_name="inbox", parent_uid=parent)
    client.complete_task(parent, "inbox")
    items = {t["uid"]: t["status"] for t in client.list_tasks("inbox")}
    assert items[parent] == "COMPLETED"
    assert items[child] == "NEEDS-ACTION"  # independent completion (D3)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd es && uv run pytest tests/test_tasks_client.py::test_children_of_lists_subtasks tests/test_tasks_client.py::test_delete_parent_without_force_raises tests/test_tasks_client.py::test_delete_parent_force_removes_children tests/test_tasks_client.py::test_delete_leaf_needs_no_force tests/test_tasks_client.py::test_done_parent_leaves_children_open -v`
Expected: FAIL — `children_of` not defined; `delete_task()` takes no `force` kwarg.

- [ ] **Step 3: Implement**

In `es/es/tasks_client.py`, add `children_of` and replace `delete_task`:

```python
    def children_of(self, uid, list_name):
        out = []
        for todo in self._calendar(list_name).todos(include_completed=True):
            if self._read_parent(todo.icalendar_component) == uid:
                out.append(todo)
        return out

    def delete_task(self, uid, list_name, force: bool = False):
        todo = self._find(uid, list_name)
        children = self.children_of(uid, list_name)
        if children and not force:
            raise HasSubtasks(
                f"Task has {len(children)} subtask(s); pass --force to delete it and them")
        for child in children:
            child.delete()
        todo.delete()
```

(Note: `test_done_parent_leaves_children_open` uses existing `complete_task` — it should already pass once `parent` is read; if it passes immediately at Step 2 that's fine, keep it as a regression guard.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd es && uv run pytest tests/test_tasks_client.py -v`
Expected: PASS (all client tests).

- [ ] **Step 5: Commit**

```bash
git add es/es/tasks_client.py es/tests/test_tasks_client.py
git commit -m "feat(es-tasks): children_of + guarded cascade delete_task(force)"
```

---

### Task 5: Capability verbs — add/edit --parent, delete --force, parent in list

**Files:**
- Modify: `es/es/capabilities/tasks.py`
- Test: `es/tests/test_tasks.py`

- [ ] **Step 1: Write the failing tests**

Add to `es/tests/test_tasks.py` (top import already has `MagicMock`, `runner`, `main`):

```python
from es.tasks_client import ParentNotFound, HasSubtasks


def test_add_forwards_parent(fake_client):
    fake_client.add_task.return_value = "child-uid"
    res = runner.invoke(main.app, ["tasks", "add", "Book hotel", "--parent", "p1"])
    assert res.exit_code == 0
    assert fake_client.add_task.call_args.kwargs["parent_uid"] == "p1"


def test_add_parent_not_found_envelope(fake_client):
    fake_client.add_task.side_effect = ParentNotFound("parent task not found: p9")
    res = runner.invoke(main.app, ["tasks", "add", "x", "--parent", "p9"])
    body = json.loads(res.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "parent_not_found"


def test_edit_forwards_parent(fake_client):
    res = runner.invoke(main.app, ["tasks", "edit", "u1", "--parent", "p2"])
    assert res.exit_code == 0
    assert fake_client.edit_task.call_args.kwargs["parent_uid"] == "p2"


def test_edit_detach_parent(fake_client):
    res = runner.invoke(main.app, ["tasks", "edit", "u1", "--parent", ""])
    assert res.exit_code == 0
    assert fake_client.edit_task.call_args.kwargs["parent_uid"] == ""


def test_list_passes_through_parent(fake_client):
    fake_client.list_tasks.return_value = [
        {"uid": "p", "summary": "Parent", "status": "NEEDS-ACTION", "tags": [], "parent": None},
        {"uid": "c", "summary": "Child", "status": "NEEDS-ACTION", "tags": [], "parent": "p"},
    ]
    res = runner.invoke(main.app, ["tasks", "list", "--list", "TODO", "--all"])
    body = json.loads(res.stdout)
    parents = {t["uid"]: t["parent"] for t in body["data"]}
    assert parents == {"p": None, "c": "p"}


def test_delete_force_forwarded(fake_client):
    res = runner.invoke(main.app, ["tasks", "delete", "u1", "--force"])
    assert res.exit_code == 0
    assert fake_client.delete_task.call_args.kwargs["force"] is True


def test_delete_has_subtasks_envelope(fake_client):
    fake_client.delete_task.side_effect = HasSubtasks("Task has 2 subtask(s); pass --force to delete it and them")
    res = runner.invoke(main.app, ["tasks", "delete", "u1"])
    body = json.loads(res.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "has_subtasks"
    assert "2 subtask" in body["error"]["message"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd es && uv run pytest tests/test_tasks.py -k "parent or force or detach or has_subtasks" -v`
Expected: FAIL — `--parent`/`--force` are unknown options; `force`/`parent_uid` not forwarded.

- [ ] **Step 3: Implement**

In `es/es/capabilities/tasks.py`:

`add` — add the `--parent` option and forward it:

```python
@app.command("add")
@envelope
def add_task(ctx: typer.Context,
            summary: str = typer.Argument(...),
            list_name: str = typer.Option("TODO", "--list"),
            note: Optional[str] = typer.Option(None, "--note"),
            tag: list[str] = typer.Option(None, "--tag"),
            due: Optional[str] = typer.Option(None, "--due"),
            remind_at: Optional[str] = typer.Option(None, "--remind"),
            parent: Optional[str] = typer.Option(None, "--parent")):
    client, vault = _client()
    url = build_deeplink(vault, note) if note else None
    uid = client.add_task(
        summary, list_name, url=url,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        due=datetime.fromisoformat(due) if due else None,
        tags=list(tag) if tag else None,
        parent_uid=parent,
    )
    return {"uid": uid}
```

`edit` — add `--parent` and forward as `parent_uid`:

```python
@app.command("edit")
@envelope
def edit_task(ctx: typer.Context,
             uid: str = typer.Argument(...),
             list_name: str = typer.Option("TODO", "--list"),
             summary: Optional[str] = typer.Option(None, "--summary"),
             tag: list[str] = typer.Option(None, "--tag"),
             due: Optional[str] = typer.Option(None, "--due"),
             remind_at: Optional[str] = typer.Option(None, "--remind"),
             parent: Optional[str] = typer.Option(None, "--parent")):
    client, _ = _client()
    client.edit_task(
        uid, list_name,
        summary=summary,
        due=datetime.fromisoformat(due) if due else None,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        tags=list(tag) if tag else None,
        parent_uid=parent,
    )
    return {"uid": uid, "edited": True}
```

`delete` — add `--force` and forward it:

```python
@app.command("delete")
@envelope
def delete_task(ctx: typer.Context,
               uid: str = typer.Argument(...),
               list_name: str = typer.Option("TODO", "--list"),
               force: bool = typer.Option(False, "--force")):
    client, _ = _client()
    client.delete_task(uid, list_name, force=force)
    return {"uid": uid, "deleted": True}
```

(`list` needs no change — the `parent` field flows through from `client.list_tasks`.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd es && uv run pytest tests/test_tasks.py -v`
Expected: PASS (new + existing capability tests; note `test_delete_reports_deleted` still passes because `delete_task` is now called with `force=False` by default — that test asserts `delete_task("u1", "TODO")` positionally; confirm it still matches. If it now fails on the keyword, update it to `fake_client.delete_task.assert_called_once_with("u1", "TODO", force=False)`).

- [ ] **Step 5: Run the whole es suite**

Run: `cd es && uv run pytest -q`
Expected: PASS (all es tests).

- [ ] **Step 6: Commit**

```bash
git add es/es/capabilities/tasks.py es/tests/test_tasks.py
git commit -m "feat(es-tasks): add/edit --parent + delete --force verbs"
```

---

### Task 6: AGENTS.md discoverability — `--parent` example

**Files:**
- Modify: `scripts/configure.py` (the `_AGENTS_PLATFORM_TEMPLATE` task section, around lines 178–183)
- Test: `scripts/tests/test_configure.py` (run; additive)

- [ ] **Step 1: Add the example**

In `scripts/configure.py`, in the `### Tasks — CalDAV` examples block, add a `--parent` line after the `es tasks done <uid>` example:

```python
    es tasks add "Buy milk" --list TODO
    es tasks add "Review Q4 plan" --note "Projects/Q4.md"   # defaults to TODO
    es tasks add "Book hotel" --parent <uid>                # subtask of <uid> (only when asked)
    es tasks list                                            # open items in TODO
    es tasks lists                                           # all lists + counts
    es tasks done <uid>
```

- [ ] **Step 2: Run configure tests**

Run: `python3 -m pytest scripts/tests/test_configure.py -q`
Expected: PASS (16 tests). If a test asserts the exact AGENTS body, update it additively to include the new line; do not remove existing assertions.

- [ ] **Step 3: Commit**

```bash
git add scripts/configure.py scripts/tests/test_configure.py
git commit -m "feat(agents): document es tasks --parent for subtask discoverability"
```

---

### Task 7: Skills — subtask behavior (`todos` + `checklists`)

**Files:**
- Modify: `.devm/.everstone/hermes/profiles/everstone/skills/todos/SKILL.md` (profile dir — gitignored, NOT committed)
- Modify: `.devm/.everstone/hermes/profiles/everstone/skills/checklists/SKILL.md` (profile dir — gitignored, NOT committed)

> These are operator content in the mounted profile dir (like `calendar`); they are not part of the git repo and have no unit test — they're validated by the agent loading + using them. Author the sections directly.

- [ ] **Step 1: Read the current skills** to find a sensible insertion point (after the Quick Reference / before the numbered Rules).

Run: `sed -n '1,200p' .devm/.everstone/hermes/profiles/everstone/skills/todos/SKILL.md`

- [ ] **Step 2: Add a "Subtasks" section to `todos/SKILL.md`**

Insert this section (before the final Rules, or after the Quick Reference):

```markdown
## Subtasks (only when asked)

A TODO can hold **one level** of subtasks. Use them **only on an explicit
request** — "break this into steps", "add *book hotel* under *beach trip*",
"give that task subtasks". **Never auto-decompose** a plain "add X" into
subtasks — a normal task stays flat.

- Create a subtask: `es tasks add "Book hotel" --parent <parent-uid>` — the
  child is filed under the parent automatically (same list; you don't pass
  `--list`).
- One level only: don't add subtasks to a subtask.
- **Render nested** when you report back, e.g.
  `Beach trip — ☐ book hotel · ☐ pack · ☐ arrange dog-sitter`. Group children
  under their parent (the `list` output gives each item a `parent` uid).
- Completing is **independent**: marking the parent done does not close the
  subtasks, and vice-versa — `es tasks done <uid>` affects only that one.
- **Deleting a parent** with subtasks is refused (`has_subtasks` error). When
  the user asks to delete a parent, **confirm first** — "That has 3 subtasks;
  delete all of them?" — then re-run with `--force`:
  `es tasks delete <uid> --force`. Deleting a single subtask needs no flag.
```

- [ ] **Step 3: Add a brief note to `checklists/SKILL.md`**

Insert near the existing rules:

```markdown
## Grouping with subtasks (optional, on request)

If the user wants a checklist's items grouped under a heading ("put these
under a *Saturday* heading"), make the heading a task and add the items as
**subtasks** of it: `es tasks add "<item>" --parent <heading-uid>`. Same rules
as elsewhere — one level, only when asked, and deleting the heading needs
`--force` (confirm first, since it takes the items with it).
```

- [ ] **Step 4: No commit** (these files are gitignored profile content). Note in the task tracker that the skills were updated in the profile dir.

---

### Task 8: Rebuild, live smoke, e2e

**Files:** none (verification only).

- [ ] **Step 1: Rebuild the container**

Run: `just dev`
Expected: `OK  ← /health reachable`.

- [ ] **Step 2: Verify the Telegram allowlist did NOT regress** (this path has broken twice before)

Run: `docker exec everstone sh -c 'tac /opt/data/hermes/profiles/everstone/logs/gateway.log | grep -m1 -E "No user allowlists|Gateway running with"'`
Expected: `Gateway running with 1 platform(s)` and **no** "No user allowlists" warning. If the warning appears, STOP and investigate before continuing.

- [ ] **Step 3: Live smoke — subtasks round-trip against the running Radicale**

```bash
docker exec everstone sh -c '
set -e
P=$(es tasks add "Smoke parent" --list TODO | python3 -c "import sys,json;print(json.load(sys.stdin)[\"data\"][\"uid\"])")
es tasks add "Smoke child" --parent "$P"
echo "--- list (child should have parent=$P) ---"
es tasks list --list TODO --all
echo "--- delete parent without --force (expect has_subtasks error) ---"
es tasks delete "$P" --list TODO || true
echo "--- delete parent with --force ---"
es tasks delete "$P" --list TODO --force
'
```
Expected: the child item shows `"parent": "<P>"`; the no-force delete returns `{"ok": false, "error": {"code": "has_subtasks", ...}}`; the `--force` delete returns `{"ok": true, ...}` and removes both. (The smoke task is cleaned up by the `--force` delete.)

- [ ] **Step 4: e2e — group-gating unchanged**

Run: `cd e2e && <the existing e2e test command, e.g. uv run pytest test_access_control.py -q>` (use the repo's established e2e invocation).
Expected: PASS — `es tasks` is still the only group-allowed capability; subtask flags don't change the access_hook argv check.

- [ ] **Step 5: Final commit (if any verification-driven fixes were needed)** — otherwise nothing to commit here.

---

## Self-Review

- **Spec coverage:** add `--parent` (Task 2 client + Task 5 verb), edit `--parent`/detach (Task 3 + Task 5), `list` `parent` field (Task 1 + Task 5 passthrough), `delete` reject-unless-`--force` cascade (Task 4 + Task 5), `done` unchanged + independent (Task 4 regression test), `RELATED-TO;RELTYPE=PARENT` (Task 1/2), child-in-parent's-list (Task 2), `parent_not_found`/`has_subtasks` error codes via `es_code` (Task 1 + Task 5), skills `todos`/`checklists` (Task 7), `AGENTS.md` wiring (Task 6), Radicale + mock tests (Tasks 1–5), access_hook unchanged (Task 8). All spec sections map to a task.
- **Out-of-scope honored:** no auto-decompose (skill says never), no depth>1 (skill says one level; CLI does not hard-block — general mechanism), no roll-up/cascade completion (done unchanged; D3 test), no `--tree` flag (agent groups flat `parent` output).
- **Type/name consistency:** `parent_uid` (client kwarg) ↔ `--parent`/`parent` (Typer) → `parent_uid=parent` forwarded; `force` kwarg ↔ `--force`; `_read_parent`/`_find_in_any_list`/`children_of`; `ParentNotFound.es_code="parent_not_found"`, `HasSubtasks.es_code="has_subtasks"` ↔ `@envelope` reads `e.es_code`; `list_tasks` adds `"parent"` consumed by tests + skills.
- **Watch:** Task 5 Step 4 flags the one existing test (`test_delete_reports_deleted`) that may need its assertion updated for the new `force=False` default — handle additively.
