# EverStone Tasks — `es tasks` capability + `todos`/`shopping`/`checklists` skills

**Status:** Approved design (brainstorming). Next: writing-plans → implementation.

**Goal:** Expose the full CalDAV task model through `es tasks`, and teach the agent
to use it via three focused skills — `todos`, `shopping`, `checklists` — matching
three distinct user workflows over one shared, flat list model.

---

## Context

- **`es tasks` today** is thin: `list`/`add`/`done`/`delete` on a single default
  list (`inbox`). Backed by Radicale (CalDAV VTODO) via
  `everstone_tasks.TasksClient` (in-process), with optional Obsidian note-links
  (`deeplink.build_deeplink`).
- **Consuming apps:** Tasks.org (Android) + an undecided Mac CalDAV app. Tasks.org's
  model is **flat Lists + Tags (`CATEGORIES`) + client-side Filters** — no
  folders-of-lists; it supports subtasks, due dates, and reminders. Staying to
  standard CalDAV keeps any Mac app coherent.
- **The skill pattern** (from the existing `calendar` SKILL.md): frontmatter
  (name/description/tags/`prerequisites: [es]`) + behavioral sections (When to
  Use / When NOT, decision-making, a command Quick Reference, numbered Rules).
  Rich "how to behave," not just a cheat-sheet.

## Decisions

- **D1 — Grouping = tags, not folders.** Tasks.org has no folder-that-contains-lists,
  so lists stay **flat** and cross-cutting grouping is done with **tags
  (`CATEGORIES`) on items** (e.g. `#errand`, `#travel`).
- **D2 — List taxonomy** (three user-facing concepts; all flat CalDAV lists underneath):
  - **`TODO`** — the main catch-all, **never deleted**; the default `add` target.
  - **🛒 Shopping lists** — persistent, marked by a **leading `🛒 ` prefix** in the
    list name (e.g. `🛒 Costco`). After a trip they are **cleared, never deleted**.
    The marker can be added/removed on explicit request.
  - **Checklists** — **ad-hoc** named lists (e.g. `Beach packing`); created →
    run down → **deleted** when done.
- **D3 — Clear semantics = completed items only by default.** `clear` removes the
  done (bought) items and keeps anything not-yet-done (carries to next trip).
  `--all` wipes the whole list. (Keeps the list either way.)
- **D4 — Reminders/due-dates = TODOs only.** The CLI supports `--due`/`--remind`
  generally, but only the `todos` skill uses them; `shopping`/`checklists` don't.
- **D5 — Tool is a general mechanism; policy lives in the skill (or config if
  hard-enforced).** The CLI does **not** special-case `TODO` or 🛒 — `list-delete`
  deletes any list, `clear` clears any list. All safety (never delete `TODO`,
  clear-not-delete shopping, confirm destructive ops) lives in **skill
  instructions**. If hard enforcement is ever needed (a list the agent must not
  delete regardless of instructions), it becomes a `config.yaml` setting the CLI
  reads — same pattern as `es cal`'s read-only-calendar policy. **Out of scope now.**
- **D6 — Packaging = three skills over one shared capability** (`todos`, `shopping`,
  `checklists`), each triggering on its own vocabulary, mirroring the single
  `calendar` skill. The capability expansion is shared; the skills are thin
  behavioral docs.
- **D7 — Group gating unchanged.** `es tasks` stays the sole group-chat-allowed
  capability (`GROUP_SAFE=True`), gated **wholesale** (any `es tasks` subcommand)
  so the family can manage shared lists in group chats. Destructive list ops in
  groups are acceptable (the skill confirms; worst case is recoverable).

## Architecture

```
es tasks  (one CLI, group-allowed)         ← shared primitive layer (mechanism only)
  └── everstone_tasks.TasksClient (CalDAV / Radicale)

skills (behavior; loaded by relevance):
  todos       → manages the TODO catch-all; due/reminders/tags; daily driver
  shopping    → 🛒 store lists; clear-after-trip
  checklists  → ad-hoc lists; create → run down → delete
```

## The `es tasks` capability (primitives)

Lists are flat CalDAV collections. Items carry a summary, tags (`CATEGORIES`),
optional due (`DUE`) + reminder (`VALARM`), an optional Obsidian note-link, and a
status. Output is the existing **JSON envelope** (`{"ok": …, "data": …}`).

| Verb | Purpose |
|---|---|
| `es tasks lists` | Enumerate lists — **raw** `[{name, open_count, total_count}]`. The agent interprets the 🛒 marker / `TODO` name itself. |
| `es tasks add <summary> [--list TODO] [--tag T]… [--due W] [--remind W] [--note N]` | Add an item. **Default list `TODO`.** Tags repeatable. `--due`/`--remind` used by TODOs. `--note` → Obsidian deeplink. |
| `es tasks list [--list TODO] [--tag T] [--all]` | List items — open only by default; `--all` includes done; filter by list/tag. |
| `es tasks done <uid>` / `es tasks delete <uid>` | Complete / remove an item. |
| `es tasks edit <uid> [--summary…] [--due…] [--remind…] [--tag…]` | Edit an item (reschedule a TODO, retag). |
| `es tasks list-create <name>` | Create a list (checklists; shopping when the name carries 🛒). |
| `es tasks list-delete <name>` | Delete a list. **General** — no tool-level special-casing (see D5). |
| `es tasks clear <list> [--all]` | Empty a list but keep it — **completed-only by default**, `--all` wipes. |

**`TasksClient` additions** (in `everstone_tasks`): enumerate collections, create
list, delete list, clear list (completed|all), tags on add/edit, due + reminder
(`VALARM`), edit item. The existing default `inbox` becomes **`TODO`** (the client
ensures `TODO` exists).

## The three skills

**`todos`** — the daily driver.
- Default home **`TODO`** (never deleted). Add/list/done tasks.
- Sets **due dates + reminders** when time-relevant; tags for grouping; attaches an
  Obsidian note-link when there's a related note.
- Distinguishes a **task reminder** (the user's app notifies them — passive) from a
  **cron** (the agent acts later) — like `calendar`'s cron note.
- Routes generic "add X" / "remind me to" → `TODO`; defers shopping/checklist
  phrasing to those skills.
- Rule: **never `list-delete` `TODO`.**

**`shopping`** — store lists.
- 🛒-marked lists (`🛒 Costco`, `🛒 Groceries`). Creates them with the 🛒 marker;
  recognizes existing ones by the marker in `es tasks lists`.
- Adds items to the right store (infers from context, names which).
- **After a trip → `clear` (completed) the list; never `list-delete` a 🛒 list.**
- No reminders.

**`checklists`** — ad-hoc lists.
- Creates a named list (`Beach packing`); adds items; ticks them off; reports "what's left."
- **`list-delete` when the checklist is done — confirm first** (read back).
- No reminders, no 🛒.

All three: confirm destructive deletes; report which list was used so the user can
correct routing.

## Migration

- Default list `inbox` → **`TODO`**; the client ensures `TODO` exists on first use.
- **No automatic data migration.** `TODO` becomes the new default target; any
  pre-existing `inbox` list in the live Radicale is left untouched (the operator
  can clear/rename it manually if they want). We do not move items programmatically.
- Update `AGENTS.md`'s task line if it references the old `inbox` default.

## Testing

- **`everstone_tasks.TasksClient`** — unit tests for the new methods: enumerate
  collections, create/delete/clear (completed vs all), tags, due + reminder
  (`VALARM`), edit.
- **`es tasks` capability** — tests (mocked/real CalDAV) for each verb + JSON
  envelopes, incl. default-`TODO` routing and `clear` semantics.
- **Skills** — behavioral docs, validated by use (like `calendar`); no unit test.
- **access_hook** — unchanged (es tasks still the group-allowed tool); existing
  group-gating tests still pass.

## Out of scope / future

- **Config-driven hard list-protection** — only if skill instructions prove
  insufficient (D5).
- **Subtasks** (`RELATED-TO`) — Tasks.org supports them; not in this design.
- **Recurring tasks/reminders.**
- Splitting/merging skills further (we can extract more later if a workflow earns
  its own skill).
