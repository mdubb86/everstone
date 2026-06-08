# EverStone Subtasks — `RELATED-TO` parent/child in `es tasks` (+ `todos`/`checklists` skills)

**Status:** Approved design (brainstorming). Next: writing-plans → implementation.

**Goal:** Let the agent organize tasks into one level of parent/child **subtasks**, on
explicit request, using standard CalDAV `RELATED-TO;RELTYPE=PARENT` so Tasks.org
renders the nesting natively.

---

## Context

- **`es tasks` today is flat.** Verbs: `list`/`add`/`edit`/`done`/`delete`/`lists`/
  `list-create`/`list-delete`/`clear`. Backed by `es/es/tasks_client.py::TasksClient`
  (caldav lib, in-process); items carry summary, `CATEGORIES` tags, `DUE`, `VALARM`,
  an optional Obsidian note-link, and status. JSON-envelope output. No parent/child.
- **CalDAV models subtasks** with `RELATED-TO;RELTYPE=PARENT:<parent-uid>` on the
  **child** VTODO; child and parent live in the **same collection (list)**.
  **Tasks.org** (the consuming Android app) renders this as nested subtasks for free.
  The earlier `es tasks` design ([2026-06-07-es-tasks-and-skills-design.md]) listed
  subtasks as out-of-scope; this spec adds them.
- **Behavior lives in skills** (`todos`/`shopping`/`checklists` in the profile dir),
  the CLI is a general mechanism (spec D5 of the tasks design). This spec keeps that
  split.

## Decisions

- **D1 — On-request only.** The agent creates subtasks **only when explicitly asked**
  ("break this into steps", "add X under Y"). It **never auto-decomposes** a task.
- **D2 — One level.** A task may have subtasks; a subtask does not get its own
  subtasks. One level by **convention** (skill behavior + display) — the CLI does not
  hard-block deeper nesting (general mechanism), but the agent never creates it.
- **D3 — Independent completion.** Completing a parent does **not** complete its
  children, and completing all children does **not** auto-complete the parent. `done`
  is unchanged — it marks exactly one task.
- **D4 — Delete is guarded.** `delete <uid>` on a task **with** children is **rejected**
  unless `--force` is passed; `--force` **cascade-deletes** the task and its subtasks.
  The CLI enforces the guard (no accidental subtree wipe); the **skill** supplies the
  human confirm (it hits the rejection, reads back "this has N subtasks, delete all?",
  then re-runs with `--force`). Deleting a leaf subtask needs no flag.
- **D5 — Output stays flat + a `parent` field.** `es tasks list` keeps its flat array;
  each item gains `"parent": "<uid>"` or `null`. The agent groups children under
  parents for display. (Rejected: a nested `children` tree — changes the output shape,
  breaks existing consumers/tests, negligible gain at one level.)
- **D6 — Skills: `todos` + `checklists` only.** `shopping` lists stay flat (no
  subtasks). Only `todos` and `checklists` document subtask use.

## Architecture

```
es tasks (CLI, general mechanism)
  └── es.tasks_client.TasksClient (caldav / Radicale)
        RELATED-TO;RELTYPE=PARENT on the child VTODO (same list as parent)

skills (behavior; on-request only):
  todos       → break a TODO into one level of subtasks when asked; render nested
  checklists  → optionally group items under a heading task when asked
  shopping    → unchanged (flat)
```

## The `es tasks` capability (changes)

| Verb | Change |
|---|---|
| `es tasks add "<child>" --parent <uid>` | **New flag.** Creates the task as a subtask of `<uid>`. The child is placed in the **parent's list** — the CLI resolves the parent's list from the uid; `--list` is ignored when `--parent` is given. Sets `RELATED-TO;RELTYPE=PARENT`. **Errors** (`{"ok": false, "error": {"code": "parent_not_found", …}}`) if the parent uid isn't found in any list. Other `add` flags (`--tag`/`--due`/`--remind`/`--note`) still apply to the child. |
| `es tasks edit <uid> --parent <uid>` | **New flag.** Re-parents an existing task (moves it into the new parent's list if needed, sets `RELATED-TO`). `--parent ""` **detaches** (removes `RELATED-TO`, promoting the task to top-level). |
| `es tasks list […]` | Each item gains `"parent": "<uid>"` or `null`. Existing fields and `--list`/`--tag`/`--all` filters unchanged. |
| `es tasks delete <uid> [--force]` | Without `--force`: deletes a childless task; **rejects** a task with children (`{"ok": false, "error": {"code": "has_subtasks", "message": "Task has N subtask(s); pass --force to delete it and them"}}`). With `--force`: cascade-deletes the task **and its subtasks**. |
| `es tasks done <uid>` | **Unchanged** (independent completion — D3). |

### `TasksClient` additions (`es/es/tasks_client.py`)

- **Write `RELATED-TO`** on `add_task` (new `parent_uid` arg) and `edit_task` (new
  `parent_uid` arg: a uid sets it, `""`/empty detaches by removing the property).
- **Read `parent`** in `list_tasks`: parse `RELATED-TO` (the value where
  `RELTYPE=PARENT`, defaulting to PARENT when `RELTYPE` is absent) → `"parent"` field
  (uid string or `None`).
- **Resolve a task's list by uid** — a helper that scans collections for a uid and
  returns the `(todo, list_name)`. Used to (a) place a `--parent` child in the
  parent's list, and (b) find children for cascade.
- **`children_of(uid)`** — list the VTODOs whose `RELATED-TO` parent == `uid` (within
  the parent's list).
- **Guarded delete** — `delete_task(uid, …, force=False)`: if the task has children
  and `force` is False, raise a typed error the capability maps to `has_subtasks`;
  if `force`, delete the children then the task.

## The skills (behavior)

**`todos`** (add a subtasks section):
- When the user explicitly asks to break a task down ("split this into steps", "add
  *book hotel* under *beach trip*"), create **one level** of subtasks under the TODO
  with `add --parent <uid>`. **Never auto-decompose** — a plain "add X" stays a flat
  task.
- **Render nested** in replies: `Beach trip — ☐ book hotel ☐ pack ☐ dog-sitter`.
- **Deleting a parent:** the agent will hit the `has_subtasks` rejection; it must
  **confirm** ("That has 3 subtasks — delete all of them?") and only then re-run with
  `--force`.

**`checklists`** (note):
- May group a checklist's items under a heading task on request (same one-level,
  on-request, confirm-before-`--force`-delete rules). Otherwise unchanged.

**`shopping`**: unchanged — flat lists, no subtasks.

## Wiring

- **`AGENTS.md`** (always-loaded, rendered by `scripts/configure.py`'s task section):
  add `--parent` to the `es tasks` examples (e.g. `es tasks add "book hotel" --parent
  <uid>`). Skills are load-on-demand, so the always-loaded reference must surface the
  subtask flag for discoverability — the same reason the reminder rule lives there.

## Testing

- **`TasksClient`** (Radicale-fixture round-trip, the existing pattern): add child →
  `RELATED-TO;RELTYPE=PARENT` present and child is in the parent's list; `list_tasks`
  reports the right `parent`; re-parent changes it; `--parent ""` detaches; delete a
  parent without force **raises**; delete with force removes parent **and** children;
  `done` on a parent leaves children untouched (D3).
- **`es tasks` capability** (mock `TasksClient`): `add --parent` routing + parent-not-
  found error; `edit --parent`/detach; `list` includes `parent`; `delete` rejection
  envelope vs `--force` cascade. JSON envelopes for each.
- **Skills:** behavioral docs, validated by use (like the others) — no unit test.
- **access_hook:** unchanged — `es tasks` stays the group-allowed tool; existing
  group-gating tests still pass.

## Out of scope / future

- **Auto-decomposition** (agent breaking tasks down on its own) — D1.
- **Depth > 1** (subtasks of subtasks) — D2.
- **Completion roll-up / cascade** (parent↔children auto-complete) — D3.
- **A `--tree` render flag** — the agent groups the flat `parent` output (D5); add only
  if rendering proves unreliable.
