# Design — `es` agent tool-gateway CLI

**Date:** 2026-06-06
**Status:** Approved design (brainstorming complete) → next: writing-plans
**Supersedes:** `docs/agent-tool-cli-spec.md` (informal working spec from the same session)

## Summary

A single agent-facing **tool-gateway CLI** named **`es`** — the one sanctioned
interface the Hermes assistant uses for every EverStone capability. It
consolidates today's fragmented agent tools (`gcal` calendar wrapper +
`everstone-tasks` CalDAV CLI) into one Typer CLI with `cal` and `tasks`
sub-apps. It is the choke point where config access, output format, and access
policy are solved once. It stays **separate from the operator `everstone`
admin CLI** (`auth`/`backup`/`setup`/`status` remain operator-only).

## Goals

- One consistent, discoverable, JSON-emitting interface for the agent.
- In-process Python (no shelling out to sub-tools); each capability is a library.
- Least-privilege, auditable group-chat gating via the existing access_hook.
- Easy to extend: adding a capability = one module + one mount line.
- Shared Google auth across all Google surfaces (Calendar now; Mail/Drive later).

## Non-goals

- Replacing the operator/admin CLI (separate concern; rename is a follow-up).
- Model-layer tool schemas (we accept runtime validation + a correction loop —
  see Security/Output).
- Group-policy-in-config (allowlist stays hardcoded).

## Architecture — two surfaces

EverStone integrates with Hermes through two different mechanisms, by design:

**A) Plugin — in-process GUARD (the existing `access_hook`).**
- A pip package loaded into Hermes via the `hermes_plugins` entry point.
- Hermes calls `pre_tool_call(tool_name, **kwargs)` before *every* tool. Return
  `None` → allow; `{"action":"block","message":…}` → deny.
- Reads `HERMES_SESSION_KEY` (`agent:main:<platform>:<chat_type>:<chat_id>`) for
  DM-vs-group; gets tool name + args via kwargs.
- Gates only — does no work.

**B) CLI — out-of-process WORKER (`es`).**
- A normal binary on PATH; the agent runs it via Hermes' `terminal` tool.
- No Hermes context — just `argv` + `/opt/config.yaml`. Does the work.
- Python (Typer), installed in the image, symlinked to `/usr/local/bin/es`.

**Flow:** agent emits `es tasks add …` → `pre_tool_call` gates it (DM? allow;
group? only `es tasks`) → terminal tool spawns `es` → `es` reads config, calls
the capability library, prints JSON → Hermes feeds JSON back to the agent.
**Plugin gates, CLI executes.**

## The `es` CLI

- **Structure (D4):** explicit Typer sub-app registry. Each capability is a
  self-contained module exposing a Typer sub-app; `main` imports + mounts each
  explicitly (no auto-discovery). Adding a tool = write the module + one mount
  line.
- **Capability contract** — each module declares:
  - its verbs (subcommands),
  - the Google scope it needs (if any) — feeds the shared-auth scope union,
  - `group_safe` (informational; the hardcoded allowlist is the enforcement),
  - which `config.yaml` keys it reads.
- **Config access (D1):** `es` reads `/opt/config.yaml` directly (mounted). No
  envdir for tools. The CLI owns its own load + the few derived constants that
  today come from `configure.py` (e.g. the CalDAV URL `http://localhost:5232`,
  the XDG/data paths). It's our own app — no per-capability secret walling.
- **In-process libraries (no sub-shelling):**
  - `es cal` → `google-api-python-client` (Google Calendar API).
  - `es tasks` → `everstone_tasks.client.TasksClient` (existing caldav-based lib)
    + `everstone_tasks.deeplink`.

## Capabilities (v1)

**`es cal`** — verbs: `agenda` (range read), `search`, `conflicts`, `add`,
`edit`, `delete`. *Dropped* `calw`/`calm` (ASCII views, useless as JSON).
- **Talks to the Google Calendar API directly** — gcalcli is dropped entirely.
- Timezone is a real API parameter: home default from config + per-call `--tz`
  (ports the behavior built into the old `gcal` wrapper).
- Read-only-calendar enforcement ports from the wrapper: writes to calendars in
  the read-only set are refused before hitting the API.
- Calendar names → IDs resolved from config's read_only/read_write lists.

**`es tasks`** — verbs: `add`, `list`, `done`, `delete`.
- Reuses `TasksClient` (caldav → Radicale) in-process; `deeplink` for
  `obsidian://` links. The old argparse `cli.py` / `mcp.py` are superseded.

## Shared Google auth

- **One credential** shared across all Google surfaces. Scopes = **union of
  enabled Google capabilities** (today: Calendar only). Least-privilege.
- **The OAuth flow stays an operator action**, NOT an `es` verb — the agent
  never runs OAuth. `auth_gcal.py` generalizes into the **admin CLI** (e.g.
  `everstone auth google`), same Caddy-proxied callback, requesting the scope
  union. `es` capabilities only *consume* the stored credential. The credential
  lives in our own data-dir store (not gcalcli's oauth file, since gcalcli is
  gone).
- Adding `es mail` later: the module declares the Gmail scope → the auth flow's
  scope union grows → one re-consent; same stored credential thereafter.
- The stored credential is a full `google.oauth2.credentials.Credentials`
  (token + refresh_token + client_id + client_secret + token_uri) so each
  capability builds its API client and refreshes without extra config.

## Output contract (D2)

- **Default: compact JSON.** Success `{"ok": true, "data": …}`; failure
  `{"ok": false, "error": {"code": "...", "message": "..."}}` plus a non-zero
  exit code.
- `--pretty` pretty-prints the same JSON (human-readable).
- Schemas kept lean (only fields the agent needs); optional `--fields` filter
  later for large reads.

## Security model (verified against hermes-agent `main`)

- `pre_tool_call` is a **complete chokepoint for the agent's own tool calls** —
  `terminal`, `execute_code`, MCP tools, plugin tools, and tool calls inside
  subagents all funnel through it. The agent **cannot bypass the group allowlist
  by choosing a different tool**.
- **Allowlist (D3) stays hardcoded** — updated from `{everstone-tasks}` to gate
  `es` on `argv[1] == "tasks"` (the one group-safe verb). DM = full trust.
- **The hook must never raise** — Hermes is fail-OPEN on hook exceptions (a
  raising hook → tool allowed). The access_hook wraps its logic so any internal
  error returns BLOCK (fail closed in our own code).
- `pre_tool_call` gates invocations, not OS effects; mitigated in groups by
  forbidding shell composition and allowing only our own trusted binary.
- Hermes treats **container isolation** as the security boundary; the hook is
  policy/defense-in-depth — fine for a single-user VM.

## Migration

- Fold `scripts/gcal` → `es cal` (reimplemented against the Calendar API; TZ +
  read-only enforcement carried over). **Drop the gcalcli dependency.**
- Fold `everstone_tasks` (lib) → `es tasks`; supersede its argparse CLI + MCP.
- access_hook: allowlist → gate `es` on `argv[1]=="tasks"`; harden to never raise.
- `configure.py` / `setup_hermes` / `services/hermes/run`: retire the
  envdir-for-tools; trim `hermes/run` to inject only `HERMES_TERMINAL_CWD`
  (+ `HERMES_HOME`); drop dead vars (`EVERSTONE_AGENT_NAME`,
  `EVERSTONE_OWNER_NAME`, `TELEGRAM_OWNER_USER_ID`).
- `Dockerfile`: install `es` + symlink; remove `gcal` / `everstone-tasks`
  binaries; drop gcalcli from the pip install.
- Update `AGENTS.md` + the calendar skill
  (`profiles/everstone/skills/calendar/SKILL.md`) to call `es cal` / `es tasks`.

## Follow-ups (later phase, non-blocking)

- Rename the operator `everstone` admin CLI to clear the namespace.
- Make `just es <args>` transparently run the in-container `es` agent CLI.

## Testing

- Reuse the existing `everstone_tasks` tests (point at `TasksClient`) and the
  `access_hook` tests (update the allowlist expectation to `es tasks`).
- New `es cal` tests against a **mocked Google Calendar API** (verbs, TZ
  handling, read-only refusal, JSON envelope).
- e2e: container boots; `es cal agenda` / `es tasks list` return valid JSON;
  group-chat gating blocks non-`es-tasks` calls.
