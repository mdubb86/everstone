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
- Kept **separate from the operator admin CLI `esadmin`** (`auth`/`backup`/
  `setup`/`status`; source `scripts/everstone_cli.py`). Dev passthroughs:
  `just es <args>` runs the **agent** `es`; `just esadmin <args>` runs the admin
  CLI. (The container name and the Hermes profile are both still `everstone`.)

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
- The **OAuth flow is operator-run**: `esadmin auth google` (Caddy-proxied
  callback, `scripts/auth_gcal.py`). `es` capabilities only *consume* the stored
  credential. `es` deps must include **`google-auth-oauthlib`** (the flow lib
  gcalcli used to provide).

## Config & env model

- EverStone's `config.yaml` (gitignored — secrets) is the source for
  EverStone-specific settings. **EverStone touches the Hermes config minimally**
  — full operator flexibility. The contract is documented in
  **`docs/hermes-integration.md`**:
  - **Asserts every boot (security/structural):** `TELEGRAM_BOT_TOKEN` (→ profile
    `.env`, secret), `TELEGRAM_ALLOWED_USERS` (→ profile `config.yaml` top-level,
    which Hermes bridges to the env var the gateway reads), `terminal.backend
    local`. Via `setup_hermes`'s `hermes config set …` (Hermes routes
    secrets→`.env`, non-secrets→`config.yaml`).
  - **Seeds once (first profile creation):** `model`/`provider` from
    `config.yaml: hermes.model`; operator owns it after (`esadmin model`).
  - **Never touches:** display, reasoning, curator, compression, auxiliary,
    telegram policy, model-after-seed — all operator-owned.
- **`es` tools read `config.yaml` directly.**
- **GOTCHA (resolved):** the Telegram **allowlist** is read from the
  `TELEGRAM_ALLOWED_USERS` env var; the *correct* way to set it is the
  **top-level `TELEGRAM_ALLOWED_USERS` key** (`hermes config set` puts it in
  `config.yaml`; Hermes bridges top-level scalars → env). The nested
  `messaging.telegram.allowed_users` is **dead** (never read) — that was the
  earlier bug. A legacy `configure.py` envdir (+ the gateway's `s6-envdir` load)
  still provides the same vars as a redundant safety net (see follow-ups).
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
- Bake the `auxiliary.vision` config into `setup_hermes` (currently only live-set
  in the data dir).
- **Retire the legacy envdir safety net** so the Hermes config file + profile
  `.env` are the *sole* source: drop `configure.py`'s `generate_hermes_env`
  envdir + the gateway's `s6-envdir` load, and migrate the remaining envdir
  consumers (`auth_gcal.py`, `everstone_cli.py`'s `_load_envdir`) to read
  `config.yaml` directly. Do it with a full gateway-env audit + the allowlist
  verification (it's the var that broke before). Until then the envdir stays as
  a redundant net.
