# EverStone — Architecture & Decisions

**Living doc** — kept current as decisions change. This is the auditable, high-level
source of truth. (Feature *specs* and *plans* under `docs/superpowers/` are
point-in-time **snapshots** — they are not updated after the fact; this doc is.)

## System

EverStone is a self-hosted personal-assistant hub: a Docker container running
**Hermes** (Nous Research agent) as a **Telegram gateway**, plus CouchDB,
Radicale (CalDAV), Caddy, and the Obsidian LiveSync bridge — supervised by s6.
- **Dev:** an ephemeral SBX VM; persistent state on a host mount
  (`.devm/.everstone` ↔ container `/opt/data`). Built/run via the `Justfile`.
- **Prod:** plain `docker run` on Unraid with bind mounts.

## Agent tool surface — the `es` CLI

- **One agent-facing CLI, `es`** (Typer), is the sanctioned interface for every
  EverStone capability. Capabilities are **Typer sub-apps** in an explicit
  registry: `es tasks` (CalDAV) and `es cal` (Google Calendar). Adding a tool =
  a new module + one mount line.
- `es` **reads `/opt/config.yaml` directly** (no envdir for tools). Output is a
  **JSON envelope** (`{"ok": true, "data": …}` / `{"ok": false, "error": {code,message}}`),
  with `--pretty` for humans.
- `es cal` → **Google Calendar API directly** (`google-api-python-client`);
  gcalcli was dropped. `es tasks` → `everstone_tasks.TasksClient` (caldav), in-process.
- Kept **separate from the operator admin CLI `everstone`** (`auth`/`backup`/
  `setup`/`status`). The `just es` recipe currently passes through to `everstone`
  (admin) — see follow-ups.

## Two integration surfaces — plugin (gate) vs CLI (worker)

- **`access_hook`** is an in-process Hermes **`pre_tool_call` plugin**. It gates
  tools by chat: DM = full trust; **groups = only `es tasks`** (argv check). It
  must be in-process (it intercepts). It **fails CLOSED** in our code because
  **Hermes is fail-OPEN on hook exceptions**. Verified to be a *complete*
  chokepoint for the agent's tool calls (terminal, execute_code, MCP, plugin,
  subagent). It is **not** the security floor — **container isolation** is.
- **`es`** is an out-of-process **worker** (subprocess via the terminal tool),
  so a hang/crash can't take down the gateway. This is why EverStone is
  **CLI-first** (it earlier reverted an MCP approach): token-cheap, fault-isolated,
  operator-runnable, decoupled from Hermes' (churny) plugin API.

## Google auth

- **One shared Google credential** — JSON at
  `/opt/data/hermes/es/google-credentials.json`; scopes = **union of enabled
  Google capabilities** (today: Calendar). Adding a Google capability appends its
  scope → one re-consent.
- The **OAuth flow is operator-run**: `everstone auth google` (Caddy-proxied
  callback, `scripts/auth_gcal.py`). `es` capabilities only *consume* the stored
  credential. `es` deps must include **`google-auth-oauthlib`** (the flow lib
  gcalcli used to provide).

## Config & env model

- `config.yaml` is the **source of truth** (gitignored — holds secrets).
  `configure.py` renders it at boot into the envdir + sourceable env file,
  `SOUL.md`, `AGENTS.md`, and service configs.
- **`es` tools read `config.yaml` directly.** The **gateway + s6 services read
  env from the envdir** (`services/hermes/run` loads it).
- **GOTCHA:** the gateway reads its Telegram **allowlist from the
  `TELEGRAM_ALLOWED_USERS` env var** (only the bot *token* comes from the profile
  config). **Do not trim the gateway's envdir load** — emptying it leaves no
  allowlist, so the owner is denied and gets the unknown-user pairing flow.
- **GOTCHA:** `SOUL.md` must be rendered into the **profile dir**
  (`profiles/everstone/SOUL.md`), NOT `$HERMES_HOME/SOUL.md` — the profile's file
  shadows the global one. `AGENTS.md` is found via `HERMES_TERMINAL_CWD` (=`/opt/data`).

## Bot behavior (the soul)

Encoded in `config.yaml: agent.soul` → `profiles/everstone/SOUL.md`:
- **Concise; one tight reply per request** (no preamble/step-recap).
- **Verify before asserting** — confirm claims about real data (calendar, tasks,
  notes, files) with a tool call rather than from memory.
- **Weave concrete results into replies** ("Scheduled Coffee with Charissa for
  9:00 AM Monday at Pinehouse Coffee").
- **Ask for a location** when an event implies a place.

## Telegram session model

**One rolling session per chat** (keyed `agent:main:telegram:dm:<id>`),
continuous, with **context compression** (~50% threshold) keeping recent context
verbatim and summarizing older. It always feels like the same agent — **no "new
chat" needed**. `/new` is an opt-in built-in (fresh session); ignore it for
continuity. Removing `/new` from the slash menu is a **known limitation** (Hermes
registers its built-in commands unconditionally).

## Vision / model

Main model `openai-codex/gpt-5.5` (ChatGPT subscription). Vision is pinned to
codex via `auxiliary.vision` (provider/model + 60s/30s timeouts) — images work;
the earlier image-hang was a no-timeout issue in the auxiliary path, fixed by the
pin + timeouts. (Currently live-set in the profile config — see follow-ups.)

## Operational gotchas (auditable lessons)

- Google OAuth client must be **Web application** type (not Desktop). Redirect URI
  `<public_url>/oauth/google/callback`; no JS origins.
- **Cutover / env / dependency bugs surface only in LIVE testing** (operator-auth
  round-trip, a real Telegram message) — not unit tests. Two were caught this way:
  missing `google-auth-oauthlib`; the trimmed gateway `TELEGRAM_ALLOWED_USERS`.
- `es` is developed/tested via **uv** (`uv run` provisions Python 3.12). The bare
  VM's system Python is 3.14 — don't install project deps there.

## Snapshots (frozen reference — not living)

- `docs/superpowers/specs/2026-06-06-es-tool-gateway-cli-design.md` — `es` design.
- `docs/superpowers/plans/2026-06-06-es-core-and-tasks.md`,
  `…-es-cal-and-google-auth.md`, `…-es-cutover.md` — the TDD implementation plans.

## Open follow-ups

- `--pretty` is root-only (`es --pretty cal …`); make it per-verb (agents trail it).
- **Rename admin CLI `everstone` → `admin`, and make `just es` the agent CLI**
  (DECIDED, not yet done — needs a `just dev` rebuild):
  - `Dockerfile:139`: symlink `/scripts/everstone_cli.py` → `/usr/local/bin/admin`
    (keep the source file name); install completion as `admin.sh` and update
    `scripts/everstone_completion.sh` (`complete … everstone` → `admin`, the
    `_everstone_completion`/`_EVERSTONE_COMPLETE` names).
  - `scripts/everstone_cli.py:34`: update the Typer `help=` string
    (`docker exec … everstone everstone <command>` → `… admin <command>`).
  - `Justfile`: `just es <args>` → `docker exec … es <args>` (the **agent** CLI);
    add `just admin <args>` → `… admin`; change `chat` → `admin chat` and
    `hermes-auth` → `admin auth hermes`. (Muscle-memory change: `just es auth
    google` / `just es chat` become `just admin …`.)
  - Error hints: `es/es/google_auth.py` + `scripts/auth_gcal.py` — "everstone
    auth google" → "admin auth google".
  - **Do NOT change:** the container name, the Hermes profile name
    (`hermes -p everstone`), or `config.yaml`.
- Bake the `auxiliary.vision` config into `setup_hermes` (currently only live-set
  in the data dir).
- Drop the conservatively-kept envdir vars (`EVERSTONE_CALDAV_*`, `VAULT_NAME`)
  once confirmed unused by the gateway path.
- Least-privilege gateway env (scope to telegram + model) — deferred; a naive trim
  broke the allowlist (see Config & env GOTCHA).
