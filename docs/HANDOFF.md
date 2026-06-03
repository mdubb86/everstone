# EverStone — Execution Handoff

This repo's **design and implementation plan are complete and committed**; **no
application code has been built yet**. Execution was paused here because this
machine has no Docker — the build/e2e require it. Resume in a Docker-capable VM.

## What this is
A single self-hosted container: Obsidian notes (CouchDB/LiveSync) + CalDAV tasks
(Radicale) + git backup, with one **Hermes** agent reachable via Telegram — full
tools in the owner's DM, strictly tasks-only in any group (enforced by a
fail-closed `pre_tool_call` hook keyed on `chat_type`).

## Read these first
- **Spec:** `docs/superpowers/specs/2026-06-03-everstone-hermes-design.md`
- **Plan:** `docs/superpowers/plans/2026-06-03-everstone-hermes.md`

## How to resume (in the VM session)
1. Confirm Docker is running and there's outbound network (pypi, crates.io,
   deno.land, github) and a few GB of disk (CouchDB source build + a ~300 MB
   engraph GGUF model).
2. **Create a feature branch — do not build on `master`:**
   `git switch -c feat/hermes-hub`
3. Tell Claude Code:
   > "Execute the plan at `docs/superpowers/plans/2026-06-03-everstone-hermes.md`
   > using subagent-driven development. Start with Phase 0 — it is a verification
   > gate whose result may branch the rest of the plan."

## The one thing that gates everything: Phase 0 (Task 0)
The privacy wall assumes Hermes's `pre_tool_call` hook receives the **structured
session key** (`agent:main:{platform}:{chat_type}:{chat_id}`) and fires for the
`terminal` tool. Task 0 probes this empirically. Outcomes:
- **Structured key** → proceed as written (chat-based hook).
- **Opaque `sess_…` key** → switch to the documented fallback: a session-store
  lookup, or a separate tasks-only group bot (Task 12 + hermes service change).

Do not build Phase 2 (the hook) before Phase 0 resolves.

## Getting this repo onto the VM (no git remote yet)
Either:
- Add a remote and push: `git remote add origin <url> && git push -u origin --all`, or
- Copy the working tree (e.g. `rsync -a --exclude node_modules --exclude .pyenv
  ./ user@vm:~/everstone`). `.gitignore` already excludes `node_modules`,
  `__pycache__`, `.pyenv`, `radfire_data`.

## State at handoff
- Branch: `master` (switch to a feature branch before building).
- Working tree: clean. Latest commit: the rebuilt plan.
- Removed-by-plan (not yet executed): `radfire/`, `taskite/`, `radfire_data/`.
