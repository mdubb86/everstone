# es tasks Capability + Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `es tasks` into a full CalDAV task capability (lists/tags/due/reminders/clear) and author three agent skills (`todos`, `shopping`, `checklists`) that drive it.

**Architecture:** Grow `everstone_tasks.TasksClient` (caldav) with list-management + tag/due/edit methods, surface them as `es tasks` Typer verbs (JSON envelope, general mechanism — no TODO/🛒 special-casing per spec D5), then author three behavioral SKILL.md files into the profile skills dir (like `calendar`). `inbox`→`TODO` default.

**Tech Stack:** Python (caldav + icalendar), Typer, pytest (`uv run`), Radicale (CalDAV server), Hermes skills (markdown).

**Spec:** `docs/superpowers/specs/2026-06-07-es-tasks-and-skills-design.md` (decisions D1–D7).

**BRANCH:** Start by creating a feature branch off `master`: `git checkout -b feat/es-tasks-skills`. Do NOT implement on `master`.

**Testing approach:** `everstone_tasks` has a real **Radicale fixture** (`everstone_tasks/tests/conftest.py` → `radicale` fixture yields a base URL) AND a MagicMock pattern (`test_client_delete.py`). Use the **Radicale fixture for the new list/item methods** — round-trip (write → read back → assert) catches `icalendar` API mistakes (the `CATEGORIES`/`DUE`/`VALARM` representations are fiddly; round-trips verify them empirically). `es` capability tests mock `TasksClient`.

---

## File Structure

- `everstone_tasks/everstone_tasks/client.py` — add `list_collections`, `delete_list`, `clear_list`, tags+due on `add_task`, `edit_task`; extend `list_tasks` output.
- `everstone_tasks/tests/test_client_lists.py` — **NEW**, Radicale-fixture round-trip tests for the new methods.
- `es/es/capabilities/tasks.py` — `inbox`→`TODO` default; new verbs `lists`/`edit`/`list-create`/`list-delete`/`clear`; expand `add`.
- `es/tests/test_tasks.py` — extend (mock `TasksClient`) for new verbs + default-`TODO` + clear semantics.
- `scripts/configure.py` — AGENTS task section `inbox`→`TODO` (+ mention new verbs).
- `.devm/.everstone/hermes/profiles/everstone/skills/{todos,shopping,checklists}/SKILL.md` — **NEW** (profile data dir; persisted via mount; NOT repo-committed — see Skill Home note).

### Skill Home (decision + flagged tradeoff)
The existing `calendar` skill lives in the **profile data dir** (`$DATA_DIR/hermes/profiles/everstone/skills/calendar/SKILL.md`), which is gitignored and persisted via the host mount. Hermes discovers **profile-local skills** by their presence in that dir (calendar isn't in `EVERSTONE_SKILLS`/`hermes skills install` yet works), so no install step is needed — authoring the SKILL.md into the profile skills dir is sufficient.
**Decision:** author the three new skills into the profile skills dir to match `calendar` and the operator's "persist via mount" preference.
**Flagged tradeoff (follow-up, not this plan):** data-dir skills are NOT version-controlled and are lost if the data dir is wiped. Shipping the core skills (calendar + these three) via the repo for reproducibility is a recommended **future** decision — note it in `docs/architecture.md` follow-ups; do not solve it here.

---

## Task 1: `TasksClient.list_collections`

**Files:**
- Modify: `everstone_tasks/everstone_tasks/client.py`
- Test: `everstone_tasks/tests/test_client_lists.py` (create)

- [ ] **Step 1: Write the failing test** (`everstone_tasks/tests/test_client_lists.py`):
```python
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
```

- [ ] **Step 2: Run it, verify FAIL** (`list_collections` undefined):
`cd /Users/michael/workspace/everstone/everstone_tasks && uv run --with pytest --with radicale --with requests pytest tests/test_client_lists.py -v`

- [ ] **Step 3: Implement** in `client.py` (add method):
```python
    def list_collections(self):
        out = []
        for cal in self._principal.calendars():
            name = cal.get_display_name() if hasattr(cal, "get_display_name") else cal.name
            todos = cal.todos(include_completed=True)
            total = len(todos)
            open_ = sum(
                1 for t in todos
                if str(t.icalendar_component.get("status", "NEEDS-ACTION")) != "COMPLETED"
            )
            out.append({"name": name or cal.id, "open_count": open_, "total_count": total})
        return out
```

- [ ] **Step 4: Run it, verify PASS** (same command).

- [ ] **Step 5: Commit**
```bash
git add everstone_tasks/everstone_tasks/client.py everstone_tasks/tests/test_client_lists.py
git commit -m "feat(tasks): TasksClient.list_collections (name + open/total counts)"
```

---

## Task 2: `TasksClient.delete_list` + `clear_list`

**Files:**
- Modify: `everstone_tasks/everstone_tasks/client.py`
- Test: `everstone_tasks/tests/test_client_lists.py`

- [ ] **Step 1: Write the failing tests** (append):
```python
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
```

- [ ] **Step 2: Run, verify FAIL.** (same uv command, `tests/test_client_lists.py`)

- [ ] **Step 3: Implement** (add to `client.py`):
```python
    def clear_list(self, list_name, completed_only: bool = True) -> int:
        removed = 0
        for todo in self._calendar(list_name).todos(include_completed=True):
            status = str(todo.icalendar_component.get("status", "NEEDS-ACTION"))
            if completed_only and status != "COMPLETED":
                continue
            todo.delete()
            removed += 1
        return removed

    def delete_list(self, list_name) -> None:
        self._calendar(list_name).delete()
```

- [ ] **Step 4: Run, verify PASS.**

- [ ] **Step 5: Commit**
```bash
git add everstone_tasks/everstone_tasks/client.py everstone_tasks/tests/test_client_lists.py
git commit -m "feat(tasks): TasksClient.clear_list (completed-only default) + delete_list"
```

---

## Task 3: tags + due on `add_task`; surface in `list_tasks`

**Files:**
- Modify: `everstone_tasks/everstone_tasks/client.py`
- Test: `everstone_tasks/tests/test_client_lists.py`

- [ ] **Step 1: Write the failing test** (append) — round-trip verifies the `icalendar` representation:
```python
from datetime import datetime

def test_add_with_tags_and_due_roundtrips(radicale):
    c = TasksClient(radicale)
    c.add_task("tagged", "TODO", tags=["errand", "town"],
               due=datetime(2026, 6, 10, 17, 0))
    t = c.list_tasks("TODO")[0]
    assert set(t["tags"]) == {"errand", "town"}
    assert t["due"] is not None and t["due"].startswith("2026-06-10")
```

- [ ] **Step 2: Run, verify FAIL** (`add_task` has no `tags`/`due`; `list_tasks` has no `tags`/`due` keys).

- [ ] **Step 3: Implement.** Update `add_task` signature + body, and `list_tasks` output.
`add_task`:
```python
    def add_task(self, summary, list_name, url: Optional[str] = None,
                 remind_at: Optional[datetime] = None,
                 due: Optional[datetime] = None,
                 tags: Optional[list] = None) -> str:
        cal = self.ensure_list(list_name); uid = uuid.uuid4().hex
        todo = Todo()
        todo.add("uid", uid); todo.add("summary", summary); todo.add("status", "NEEDS-ACTION")
        if url:
            todo.add("url", url)
        if due:
            todo.add("due", due)
        if tags:
            todo.add("categories", tags)
        if remind_at:
            alarm = Alarm()
            alarm.add("action", "DISPLAY"); alarm.add("description", summary)
            alarm.add("trigger", remind_at)
            todo.add_component(alarm)
        ical = ICalendar(); ical.add("prodid", "-//everstone-tasks//EN"); ical.add("version", "2.0")
        ical.add_component(todo)
        cal.save_todo(ical=ical.to_ical().decode())
        return uid
```
`list_tasks` — add `tags` + `due` to each dict. Use a helper to read categories robustly (icalendar stores `CATEGORIES` as a `vCategory` with `.cats`, OR a list when multiple lines):
```python
    @staticmethod
    def _read_tags(c):
        if "categories" not in c:
            return []
        cats = c.get("categories")
        items = cats if isinstance(cats, list) else [cats]
        out = []
        for entry in items:
            out.extend(str(x) for x in getattr(entry, "cats", [entry]))
        return out

    @staticmethod
    def _read_due(c):
        if "due" not in c:
            return None
        dt = c["due"].dt
        return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
```
In `list_tasks`'s appended dict add: `"tags": self._read_tags(c), "due": self._read_due(c),`.

- [ ] **Step 4: Run, verify PASS.** If the round-trip shows a different `CATEGORIES`/`DUE` shape than `_read_tags`/`_read_due` assume, ADJUST the readers to match what Radicale actually returns (that's the point of the round-trip) and re-run.

- [ ] **Step 5: Commit**
```bash
git add everstone_tasks/everstone_tasks/client.py everstone_tasks/tests/test_client_lists.py
git commit -m "feat(tasks): tags (CATEGORIES) + due (DUE) on add_task; surfaced in list_tasks"
```

---

## Task 4: `TasksClient.edit_task`

**Files:**
- Modify: `everstone_tasks/everstone_tasks/client.py`
- Test: `everstone_tasks/tests/test_client_lists.py`

- [ ] **Step 1: Write the failing test** (append):
```python
def test_edit_task_updates_fields(radicale):
    c = TasksClient(radicale)
    uid = c.add_task("draft", "TODO", tags=["old"])
    c.edit_task(uid, "TODO", summary="final", due=datetime(2026, 6, 11, 9, 0), tags=["new"])
    t = [x for x in c.list_tasks("TODO") if x["uid"] == uid][0]
    assert t["summary"] == "final"
    assert t["tags"] == ["new"]
    assert t["due"].startswith("2026-06-11")
```

- [ ] **Step 2: Run, verify FAIL** (`edit_task` undefined).

- [ ] **Step 3: Implement** (add to `client.py`):
```python
    def edit_task(self, uid, list_name, summary: Optional[str] = None,
                  due: Optional[datetime] = None,
                  remind_at: Optional[datetime] = None,
                  tags: Optional[list] = None) -> None:
        todo = self._find(uid, list_name); c = todo.icalendar_component
        if summary is not None:
            c["summary"] = summary
        if due is not None:
            if "due" in c:
                del c["due"]
            c.add("due", due)
        if tags is not None:
            if "categories" in c:
                del c["categories"]
            c.add("categories", tags)
        if remind_at is not None:
            # replace any existing VALARM(s)
            for sub in [s for s in todo.icalendar_instance.subcomponents
                        if getattr(s, "name", "") == "VALARM"]:
                todo.icalendar_instance.subcomponents.remove(sub)
            alarm = Alarm()
            alarm.add("action", "DISPLAY"); alarm.add("description", c.get("summary", ""))
            alarm.add("trigger", remind_at)
            c.add_component(alarm)
        todo.save()
```
NOTE: the VALARM-replacement walks `icalendar_instance` subcomponents — if the round-trip test (Step 4) shows the alarm isn't replaced correctly, adjust to operate on `c.subcomponents` (the VTODO component) instead. Verify empirically.

- [ ] **Step 4: Run, verify PASS** (adjust per the note if needed).

- [ ] **Step 5: Commit**
```bash
git add everstone_tasks/everstone_tasks/client.py everstone_tasks/tests/test_client_lists.py
git commit -m "feat(tasks): TasksClient.edit_task (summary/due/remind/tags)"
```

---

## Task 5: `es tasks` — `TODO` default + `lists` verb

**Files:**
- Modify: `es/es/capabilities/tasks.py`
- Test: `es/tests/test_tasks.py`

- [ ] **Step 1: Write the failing test** (in `es/tests/test_tasks.py`, matching the existing mock-TasksClient pattern there — READ the file first for its fixture style):
```python
def test_add_defaults_to_TODO(monkeypatch):
    # patch _client() to a mock; assert add_task called with list_name="TODO"
    ...
def test_lists_verb_returns_collections(monkeypatch):
    # mock client.list_collections -> [{"name":"TODO","open_count":1,"total_count":2}]
    # invoke `lists`; assert envelope data matches
    ...
```
Fill these in using the exact mock/invoke style already in `es/tests/test_tasks.py` (CliRunner or direct, mocking `_client`). Keep them concrete.

- [ ] **Step 2: Run, verify FAIL.** `cd /Users/michael/workspace/everstone/es && uv run pytest tests/test_tasks.py -v`

- [ ] **Step 3: Implement.** In `es/es/capabilities/tasks.py`: change every `--list` default from `"inbox"` to `"TODO"`. Add the `lists` verb:
```python
@app.command("lists")
@envelope
def lists(ctx: typer.Context):
    client, _ = _client()
    return client.list_collections()
```

- [ ] **Step 4: Run, verify PASS.**

- [ ] **Step 5: Commit**
```bash
git add es/es/capabilities/tasks.py es/tests/test_tasks.py
git commit -m "feat(es tasks): default list TODO; add lists verb"
```

---

## Task 6: `es tasks` — expand `add` (tags/due/remind) + `edit` verb

**Files:**
- Modify: `es/es/capabilities/tasks.py`
- Test: `es/tests/test_tasks.py`

- [ ] **Step 1: Write the failing test** — assert `add` forwards `--tag`/`--due`/`--remind` to `add_task`, and `edit` calls `edit_task`. (Mock `_client`; parse `--due/--remind` as ISO via `datetime.fromisoformat`.)

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement.** Expand `add`:
```python
@app.command("add")
@envelope
def add_task(ctx: typer.Context,
            summary: str = typer.Argument(...),
            list_name: str = typer.Option("TODO", "--list"),
            note: Optional[str] = typer.Option(None, "--note"),
            tag: list[str] = typer.Option(None, "--tag"),
            due: Optional[str] = typer.Option(None, "--due"),
            remind_at: Optional[str] = typer.Option(None, "--remind")):
    client, vault = _client()
    url = build_deeplink(vault, note) if note else None
    uid = client.add_task(
        summary, list_name, url=url,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        due=datetime.fromisoformat(due) if due else None,
        tags=list(tag) if tag else None,
    )
    return {"uid": uid}
```
Add `edit`:
```python
@app.command("edit")
@envelope
def edit_task(ctx: typer.Context,
             uid: str = typer.Argument(...),
             list_name: str = typer.Option("TODO", "--list"),
             summary: Optional[str] = typer.Option(None, "--summary"),
             tag: list[str] = typer.Option(None, "--tag"),
             due: Optional[str] = typer.Option(None, "--due"),
             remind_at: Optional[str] = typer.Option(None, "--remind")):
    client, _ = _client()
    client.edit_task(
        uid, list_name,
        summary=summary,
        due=datetime.fromisoformat(due) if due else None,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        tags=list(tag) if tag else None,
    )
    return {"uid": uid, "edited": True}
```
Also surface `tags`/`due` in the `list` verb output — it already returns `client.list_tasks(...)` which now includes them, so no change needed there; confirm the test asserts they pass through.

- [ ] **Step 4: Run, verify PASS.**

- [ ] **Step 5: Commit**
```bash
git add es/es/capabilities/tasks.py es/tests/test_tasks.py
git commit -m "feat(es tasks): add --tag/--due/--remind; edit verb"
```

---

## Task 7: `es tasks` — `list-create` / `list-delete` / `clear`

**Files:**
- Modify: `es/es/capabilities/tasks.py`
- Test: `es/tests/test_tasks.py`

- [ ] **Step 1: Write the failing test** — `list-create` calls `ensure_list`; `list-delete` calls `delete_list`; `clear` calls `clear_list(completed_only=True)` by default and `completed_only=False` with `--all`.

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement** (NO TODO/🛒 special-casing — spec D5; general mechanism):
```python
@app.command("list-create")
@envelope
def list_create(ctx: typer.Context, name: str = typer.Argument(...)):
    client, _ = _client()
    client.ensure_list(name)
    return {"list": name, "created": True}


@app.command("list-delete")
@envelope
def list_delete(ctx: typer.Context, name: str = typer.Argument(...)):
    client, _ = _client()
    client.delete_list(name)
    return {"list": name, "deleted": True}


@app.command("clear")
@envelope
def clear(ctx: typer.Context,
          name: str = typer.Argument(...),
          all_: bool = typer.Option(False, "--all")):
    client, _ = _client()
    removed = client.clear_list(name, completed_only=not all_)
    return {"list": name, "removed": removed}
```
(Typer maps `--all` to the `all_` param via the option name; confirm the option name renders as `--all` — if Typer derives it from the param name use `typer.Option(False, "--all")` as shown.)

- [ ] **Step 4: Run, verify PASS.**

- [ ] **Step 5: Commit**
```bash
git add es/es/capabilities/tasks.py es/tests/test_tasks.py
git commit -m "feat(es tasks): list-create / list-delete / clear verbs"
```

---

## Task 8: AGENTS.md task section (inbox→TODO) + rebuild + live smoke

**Files:**
- Modify: `scripts/configure.py` (the AGENTS task section, ~lines 174-182)

- [ ] **Step 1: Update the AGENTS task examples.** In `scripts/configure.py`, the `_AGENTS_PLATFORM_TEMPLATE` task block currently shows `--list inbox` examples. Change them to reflect the new shape:
```
    es tasks add "Buy milk" --list TODO
    es tasks add "Review Q4 plan" --note "Projects/Q4.md"   # defaults to TODO
    es tasks list                                            # open items in TODO
    es tasks lists                                           # all lists + counts
    es tasks done <uid>
```
Keep the "Run `es tasks --help` for the full surface" line and the group-policy note (es tasks is the only group-permitted invocation). Do NOT enumerate the skill behaviors here — that's the skills' job; AGENTS just points at the tool.

- [ ] **Step 2: Update the test.** If `scripts/tests/test_configure.py` asserts the old `inbox` text in the AGENTS render, update that assertion to the new `TODO` text. Run `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py -q` — green (modulo any unrelated pre-existing rot, which should be none after the earlier cleanup).

- [ ] **Step 3: Rebuild + live smoke** against the running Radicale:
```bash
cd /Users/michael/workspace/everstone && just dev && sleep 12
docker exec everstone es tasks lists
docker exec everstone es tasks add "smoke milk" --list "🛒 SmokeStore"
docker exec everstone es tasks list --list "🛒 SmokeStore"
docker exec everstone es tasks add "smoke todo" --tag test --due 2026-06-10T09:00
docker exec everstone es tasks list   # default TODO, shows tags+due
# clear semantics:
docker exec everstone sh -c 'U=$(docker exec everstone es tasks list --list "🛒 SmokeStore" | python3 -c "import sys,json;print(json.load(sys.stdin)[\"data\"][0][\"uid\"])")'  # (or grab uid manually)
docker exec everstone es tasks clear "🛒 SmokeStore"        # completed-only (nothing done yet -> 0)
docker exec everstone es tasks list-delete "🛒 SmokeStore"  # cleanup the smoke list
```
Expected: `lists` returns JSON with counts; add/list round-trip tags+due; `clear`/`list-delete` work. Then clean up any smoke lists you created. Confirm the allowlist didn't regress (`docker exec everstone sh -c 'tac /opt/data/hermes/profiles/everstone/logs/gateway.log | grep -m1 -E "No user allowlists|Gateway running with"'` → "Gateway running with").

- [ ] **Step 4: Commit**
```bash
git add scripts/configure.py scripts/tests/test_configure.py
git commit -m "docs(agents): es tasks examples use TODO default + lists verb"
```

---

## Task 9: Author the three skills

**Files (profile data dir — NOT git-committed):**
- Create: `.devm/.everstone/hermes/profiles/everstone/skills/todos/SKILL.md`
- Create: `.devm/.everstone/hermes/profiles/everstone/skills/shopping/SKILL.md`
- Create: `.devm/.everstone/hermes/profiles/everstone/skills/checklists/SKILL.md`

First READ `.devm/.everstone/hermes/profiles/everstone/skills/calendar/SKILL.md` to match the frontmatter + section style exactly. Each skill's frontmatter: `name`, `description` (rich, with trigger vocabulary), `version: 1.0.0`, `author: EverStone`, `license: MIT`, `metadata.hermes.tags: [...]`, `prerequisites.commands: [es]`.

- [ ] **Step 1: Write `todos/SKILL.md`.** Content (write verbatim, adjusting prose to match calendar's voice):
  - **Frontmatter** description triggers on: tasks, todos, "remind me to", "add to my list", "what's on my plate". tags: `[tasks, todos, es, reminders]`.
  - **Intro:** Use `es tasks` for to-dos. Default list is **`TODO`** (the permanent catch-all). JSON envelope output.
  - **When to Use:** capturing a to-do, "remind me to X", setting a due date/reminder, reviewing "what's on my list".
  - **When NOT to Use:** shopping items → `shopping` skill; a packing/event checklist → `checklists` skill; a fixed-time calendar event → `calendar`; an action the agent must perform later → cronjob (not a task reminder).
  - **Reminders vs cron:** a task **reminder** (`--remind`) is passive — the user's app notifies *them*. A **cron** is the agent acting later. Use `--remind` for "remind me to call the dentist"; use cron for "you (agent) follow up with me Monday".
  - **Routing:** generic "add X" / "I need to…" / "remind me to…" → `TODO`. Tag with `--tag` for grouping (e.g. `#errand`, `#work`). Attach `--note "Path.md"` when there's a related Obsidian note.
  - **Quick Reference:** `es tasks add "Call dentist" --due 2026-06-10T09:00 --remind 2026-06-10T08:30`; `es tasks list`; `es tasks list --tag errand`; `es tasks done <uid>`; `es tasks edit <uid> --due …`; `es tasks lists`.
  - **Rules (numbered):** 1) Default to `TODO`; **never `list-delete` `TODO`** (it's the permanent catch-all). 2) Resolve relative dates ("tomorrow 9am") against today (Central) before passing ISO `--due`/`--remind`. 3) Use a reminder for user-notifications, cron for agent-actions. 4) Confirm before deleting a task; read it back. 5) Name the list you used in your reply.

- [ ] **Step 2: Write `shopping/SKILL.md`.**
  - **Frontmatter** triggers: shopping, grocery, store names, "add to Costco/Target", "what do we need", "shopping list". tags: `[shopping, groceries, tasks, es]`.
  - **Intro:** Shopping lists are CalDAV lists **prefixed with `🛒 `** (e.g. `🛒 Costco`, `🛒 Groceries`). They are **persistent** — you **clear** them after a trip, you do **not** delete them.
  - **Finding/creating a store list:** `es tasks lists` and look for `🛒 ` names. If the user names a store with no list yet, create it: `es tasks list-create "🛒 Costco"`. Default catch-all store list: `🛒 Groceries` when no store is named.
  - **Adding items:** `es tasks add "milk" --list "🛒 Costco"`. Infer the store from context; name which list you used.
  - **After a trip (clear):** `es tasks clear "🛒 Costco"` removes the **bought (completed)** items, keeping anything not yet checked off. Use `es tasks clear "🛒 Costco" --all` only if the user says "wipe it / clear everything".
  - **When NOT to Use:** a general to-do → `todos`; a one-off event/packing list → `checklists`.
  - **Rules:** 1) Shopping lists carry the `🛒 ` prefix; create new ones with it. 2) **Never `list-delete` a `🛒 ` list** — clear it. 3) No reminders/due-dates on shopping items. 4) Name the store list you used. 5) Adding/removing the 🛒 marker (rename) only on explicit request.

- [ ] **Step 3: Write `checklists/SKILL.md`.**
  - **Frontmatter** triggers: checklist, "packing list", "make a list for…", "beach checklist". tags: `[checklists, packing, tasks, es]`.
  - **Intro:** Checklists are **ad-hoc** CalDAV lists for a specific purpose/event (e.g. `Beach packing`). Lifecycle: **create → add items → tick them off → delete when done.**
  - **Create:** `es tasks list-create "Beach packing"` (no 🛒, no special prefix). Add items: `es tasks add "sunscreen" --list "Beach packing"`. Show progress: `es tasks list --list "Beach packing"` (open items) / `--all` (incl. done).
  - **Finishing:** when the user says the checklist is done/the trip's over, **`es tasks list-delete "Beach packing"`** — but **confirm first** (read back the list name + that it'll be removed).
  - **When NOT to Use:** recurring store shopping → `shopping` (those persist + clear); a standing to-do → `todos`.
  - **Rules:** 1) Checklists are ad-hoc, no 🛒, no reminders. 2) **Confirm before `list-delete`** — read back the list. 3) Don't delete `TODO` or any `🛒 ` list under the guise of "finishing a checklist." 4) Name the checklist you used.

- [ ] **Step 4: Verify the skills load.** Rebuild not needed (profile dir is mounted). Restart the gateway so Hermes re-scans skills, then confirm they're registered:
```bash
docker exec everstone esadmin restart hermes && sleep 8
docker exec everstone hermes -p everstone skills list 2>&1 | grep -iE "todos|shopping|checklists|calendar"
```
Expected: all four skills listed/enabled. (If `hermes skills list` isn't the right subcommand, find the equivalent — the goal is to confirm Hermes discovered the three new profile-local skills.)

- [ ] **Step 5: No git commit** (profile data dir is gitignored). Instead, note in the report that the three SKILL.md files were authored to the profile and discovered by Hermes.

---

## Task 10: End-to-end verification + architecture-doc follow-up

- [ ] **Step 1: Unit suites green.**
```bash
cd /Users/michael/workspace/everstone
(cd everstone_tasks && uv run --with pytest --with radicale --with requests pytest -q | tail -2)
(cd es && uv run pytest -q | tail -2)
(cd access_hook && uv run --with pytest pytest -q | tail -1)
python3 -m pytest scripts/tests/ -q | tail -2
```
Expected: all green (es grows by the new tasks tests; everstone_tasks grows by test_client_lists).

- [ ] **Step 2: Live agent-flow smoke** (if feasible): in a DM to the bot, "add milk to the Costco list", "what's on my todos", "make a beach checklist with towel and sunscreen" — confirm the right skill fires and the right list is used. (Manual; report what was exercised.)

- [ ] **Step 3: access_hook group-gating intact** — `cd access_hook && uv run --with pytest pytest -q` green; the e2e `test_access_control.py` still passes (`cd e2e && uv run pytest test_access_control.py -q`).

- [ ] **Step 4: Architecture doc.** Add to `docs/architecture.md`: a short note that EverStone now has a `tasks` capability with three skills (todos/shopping/checklists), and a **follow-up** that profile-local skills (calendar + these three) are not version-controlled — shipping them via the repo for reproducibility is a future decision.
```bash
git add docs/architecture.md && git commit -m "docs(architecture): tasks capability + 3 skills; flag skill-reproducibility follow-up"
```

- [ ] **Step 5: Final commit/cleanup** — ensure the branch `feat/es-tasks-skills` holds all capability + doc commits (skills live in the data dir, uncommitted by design).

---

## Self-Review notes (already applied)

- **Spec coverage:** D1 tags (Task 3), D2 taxonomy (skills T9 + general CLI), D3 clear-completed-default (Task 2/7), D4 reminders-TODOs-only (skills: only `todos` uses `--remind`), D5 general-mechanism-no-special-casing (Tasks 7 explicit), D6 three skills (Task 9), D7 group gating unchanged (Task 10 Step 3). ✓
- **Placeholder scan:** the es/test steps say "match the existing fixture style" + "fill in concretely" — these reference a real file the implementer reads; the client/skill tasks have complete code/content. The icalendar `CATEGORIES`/`VALARM` readers carry an explicit "adjust per round-trip" instruction rather than a guess-and-hope. ✓
- **Type consistency:** `list_collections`/`clear_list(completed_only=)`/`delete_list`/`edit_task(summary,due,remind_at,tags)`/`add_task(...,due,tags)` names match between client tasks and the es-verb tasks that call them. ✓
- **Skill home:** decided (profile data dir, matches calendar) + reproducibility flagged as a follow-up (Task 10 Step 4), not silently dropped. ✓
