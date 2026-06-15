# EverStone — Architecture & Decisions

**Living doc** — kept current as decisions change. This is the auditable, high-level
source of truth for EverStone's architecture and the rationale behind it.

## System

EverStone is a self-hosted personal-assistant hub: a Docker container running
**Hermes** (Nous Research agent) as a **Telegram gateway**, plus CouchDB,
Radicale (CalDAV), Caddy, and the Obsidian LiveSync bridge — supervised by s6.
- **Dev:** an ephemeral SBX VM; persistent state on a host mount
  (`.devm/.everstone` ↔ container `/opt/data`). Built/run via the `Justfile`.
- **Prod:** plain `docker run` on Unraid with bind mounts.
- **Hermes install = canonical checkout+venv** (NOT `pip install hermes-agent`,
  which is off Nous's documented path and breaks ecosystem tooling like
  hermes-webui that expects `run_agent.py` + a `venv/bin/python`). A multi-stage
  build clones `NousResearch/hermes-agent` (latest `main`) to
  `/usr/local/lib/hermes-agent`, builds a `uv venv` + `uv pip install -e '.[all]'`,
  and installs **`es` + the `access_hook` plugin + `python-telegram-bot` into that
  venv** (one interpreter). `hermes`/`es` symlink to `.venv/bin`; `esadmin` runs
  via the venv python; `radicale` stays a decoupled system install.
  `install.sh` is *not* used — it downloads `uv` from `astral.sh` (firewall-blocked
  in dev) and assumes `apt`/glibc; we replicate its steps with `uv` from PyPI on
  Alpine/musl. `HERMES_HOME=/opt/data/hermes` (the mounted state) is unchanged.

## Agent tool surface — the `es` CLI

- **One agent-facing CLI, `es`** (Typer), is the sanctioned interface for every
  EverStone capability. Capabilities are **Typer sub-apps** in an explicit
  registry: `es tasks` (CalDAV) and `es cal` (Google Calendar). Adding a tool =
  a new module + one mount line.
- `es` **reads `/opt/config.yaml` directly** (no envdir for tools). Output is a
  **JSON envelope** (`{"ok": true, "data": …}` / `{"ok": false, "error": {code,message}}`),
  with `--pretty` for humans.
- `es cal` → **Google Calendar API directly** (`google-api-python-client`);
  gcalcli was dropped. `es tasks` → `es.tasks_client.TasksClient` (caldav), in-process.
- **`es tasks` is a full CalDAV task model** — verbs `list`/`add`/`edit`/`done`/
  `delete`/`lists`/`list-create`/`list-delete`/`clear`; flat lists with optional
  **one-level subtasks** (`RELATED-TO;RELTYPE=PARENT`: `add --parent` files a child
  in the parent's list, `edit --parent` re-parents/moves or detaches, `delete`
  refuses a parent with children unless `--force` cascades; completion is
  independent), `CATEGORIES` tags, `DUE`/`VALARM`, default list **`TODO`**. It is a **general mechanism** —
  no list is special-cased in the CLI (spec D5); all task *policy* lives in three
  skills: **`todos`** (the `TODO` catch-all; due/reminders/tags), **`shopping`**
  (🛒-prefixed persistent store lists; clear-after-trip, never delete), and
  **`checklists`** (ad-hoc lists; create→run-down→delete).
- Kept **separate from the operator admin CLI `esadmin`** (ops: `status`/`logs`/
  `restart`/`backup`/`sync-state`; plus `auth` (google only — `auth hermes` was
  replaced by `model`)/`model`/`session`/`setup`/`calendars`/`chat`; source
  `scripts/everstone_cli.py`). It's a deliberately **unified operator surface**
  — it may include thin passthroughs to Hermes (`chat`, `session`) so there's
  less to remember. Dev passthroughs: `just es <args>` runs the **agent** `es`;
  `just esadmin <args>` runs the admin CLI; `just model <value>` runs
  `esadmin model`. (The container name and the Hermes profile are both still
  `everstone`.)

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
  EverStone-specific settings. **No `hermes.model` in config.yaml** — the LLM
  model is not stored there. **EverStone touches the Hermes config minimally**
  — full operator flexibility. The contract is documented in
  **`docs/hermes-integration.md`**:
  - **Asserts every boot (security/structural):** `TELEGRAM_BOT_TOKEN` (→ profile
    `.env`, secret) and `TELEGRAM_ALLOWED_USERS` (→ profile `config.yaml`
    top-level), plus `terminal.backend local`. The two Telegram values are
    enforced with **VERIFY + LOUD-FAIL on drift** via `scripts/assert_telegram.py`
    — `config.yaml` is authoritative; a divergent live value is a logged error,
    not a silent overwrite. Via `setup_hermes`'s `hermes config set …` (Hermes
    routes secrets→`.env`, non-secrets→`config.yaml`).
  - **One-time operator action (not seeded from config.yaml):** `model`/`provider`
    are set via `esadmin model <value>` (= `just model <value>`), which sets the
    model in the Hermes profile AND runs the provider's auth flow in one step.
    This replaced `esadmin auth hermes`. EverStone never re-asserts it.
  - **Never touches:** display, reasoning, curator, compression, auxiliary,
    telegram policy, model-after-set — all operator-owned.
- **`es` tools read `config.yaml` directly.**
- **GOTCHA (resolved):** the Telegram **allowlist** is read from the
  `TELEGRAM_ALLOWED_USERS` env var; the *correct* way to set it is the
  **top-level `TELEGRAM_ALLOWED_USERS` key** (`hermes config set` puts it in
  `config.yaml`; Hermes bridges top-level scalars → env). The nested
  `messaging.telegram.allowed_users` is **dead** (never read) — that was the
  earlier bug. The legacy `configure.py` envdir (+ the gateway's `s6-envdir`
  load) was retired: the Hermes profile `config.yaml` + `.env` are now the sole
  source, and `HERMES_TERMINAL_CWD` is exported directly by the gateway's s6
  run script.
- **GOTCHA:** `SOUL.md` must be rendered into the **profile dir**
  (`profiles/everstone/SOUL.md`), NOT `$HERMES_HOME/SOUL.md` — the profile's file
  shadows the global one. `AGENTS.md` is found via `HERMES_TERMINAL_CWD` (=`/opt/data`).

## Bot behavior (the soul)

Encoded in `config.yaml: agent.soul` → `profiles/everstone/SOUL.md`:
- **Concise; one tight reply per request** (no preamble/step-recap).
- **Verify before asserting** — confirm claims about real data (calendar, tasks,
  notes, files) with a tool call rather than from memory.
- **Weave concrete results into replies** ("Scheduled Coffee with Sam for
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

## Open follow-ups

- **Profile-local skills aren't version-controlled.** The agent skills (`calendar`,
  `todos`, `shopping`, `checklists`) live in the profile data dir
  (`$DATA_DIR/hermes/profiles/everstone/skills/<name>/SKILL.md`) — gitignored,
  persisted via the host mount, lost if the data dir is wiped. Shipping the core
  skills via the repo (e.g. a `skills/` dir installed at boot) for reproducibility
  is a future decision; for now they're operator content per the "persist via
  mount" preference.
- `--pretty` is root-only (`es --pretty cal …`); make it per-verb (agents trail it).
- Bake the `auxiliary.vision` config into `setup_hermes` (currently only live-set
  in the data dir).
