# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

EverStone is a self-hosted personal-assistant hub shipped as a **single Docker image** (Alpine + s6). One container runs the **Hermes** agent (NousResearch/hermes-agent) as a Telegram gateway plus the services it drives: CouchDB + the Obsidian LiveSync bridge (notes), Radicale (CalDAV tasks), Google Calendar, Caddy (reverse proxy), and an optional in-process web UI. The agent's capabilities are exposed to it as **MCP tools** (`es_*`); skills shape its behavior.

## Common commands

All dev workflow goes through the `Justfile` (run `just` to list). The container always serves on `:80`; `/opt/config.yaml` and `/opt/data` are bind mounts.

```bash
just build            # docker build (bakes version via git describe → /version)
just dev              # build + run the dev container on :80 (needs config.yaml; pings /health)
just logs             # docker logs -f
just shell            # bash inside the dev container (esadmin tab-completion works)
just es <args>        # run the AGENT's es entrypoint in the container
just esadmin <args>   # run the operator CLI in the container (status/logs/backup/auth/model/…)
just model <prov/mdl> # set LLM model + run provider auth (one-time), e.g. openai-codex/gpt-5.5
just release          # interactive semver bump → tags vX.Y.Z + pushes (CI builds/publishes)
just reset            # DESTRUCTIVE: wipe the data dir (prompts unless --yes)
```

A dev `config.yaml` is required (`cp config.example.yaml config.yaml`); it holds secrets and is gitignored.

## Tests

```bash
pytest es/tests -q                       # es unit tests  (CI: pip install -e ./es first)
pytest es/tests/test_mcp_server.py::test_es_notes_journal_ok -q   # single test
PYTHONPATH=scripts pytest scripts/tests -q   # boot-script unit tests (configure.py, esadmin, …)
just e2e                                 # full e2e: builds + boots a throwaway container
cd e2e && uv run pytest test_routing.py -v   # single e2e module
```

- **Use `uv` / Python 3.12 locally** — `es` and `e2e` target 3.12; the bare VM's system Python is 3.14, do not install project deps there.
- CI: `.github/workflows/ci.yml` runs the es + scripts unit tests on every push to `main` and on PRs (fast gate). `.github/workflows/build.yml` builds and publishes the GHCR image **only on `v*` tags** — so a release is one build, and pushing to `main` never builds an image. `:latest` always tracks the newest release.

## Architecture (the big picture)

> Authoritative design + rationale lives in **`docs/architecture.md`** (a living doc), and the **Hermes config contract** in **`docs/hermes-integration.md`** — treat those as the single source of truth. This section is a fast orientation; defer to them for depth.

**Single image, s6-supervised.** A multi-stage `Dockerfile` produces one image; `services/<name>/run` are the s6 services: `couchdb`, `radicale`, `caddy`, `livesync-bridge`, `hermes` (the gateway), `hermes-webui` (opt-in), `camofox-flex` + `camofox-auth` (the two isolated browser instances), plus `setup_*` oneshots. Hermes is installed as a **canonical checkout + uv venv** (clone `NousResearch/hermes-agent`, `uv pip install -e '.[all]'`), NOT `pip install hermes-agent` — `es`, the `access_hook` plugin, and `python-telegram-bot` are installed into that same venv so one interpreter loads everything.

**Boot = render config, then run.** Nothing in `/opt/config.yaml` is consumed by services directly. `scripts/configure.py` runs at startup, validates `config.yaml` against `config/.../schema.json`, deep-merges defaults, and **generates** every service config: CouchDB `local.ini`, the Caddyfile, Radicale htpasswd, the livesync-bridge `config.json`, the Hermes profile's `SOUL.md` and the agent's `AGENTS.md`, and `setup-obsidian-livesync`. To change runtime behavior you usually edit `config.yaml` + a generator in `scripts/configure.py`, then rebuild/restart — not the generated files. `scripts/setup_hermes` creates the `everstone` Hermes profile (`--no-skills`, minimal) and asserts only the structural keys.

**Two agent-facing surfaces (the core mental model):**
- **`es/` — the capability surface, exposed as an MCP server.** `es/es/mcp_server.py` is a FastMCP server (entry point `es-mcp`, registered in the Hermes profile under `mcp_servers: everstone-es`). It exposes the agent's tools: `es_tasks_*` (CalDAV via `tasks_client.py`), `es_cal_*` (Google Calendar API via `capabilities/cal.py` + `google_auth.py`), `es_notes_*` (Obsidian vault via `vault_client.py`), plus contacts/web-fetch tools. Every tool returns a JSON envelope (`{ok, data}` / `{ok, error:{code,message}}`). It reads `/opt/config.yaml` directly. **`es` is MCP-only now** — there is no `es` Typer CLI; the agent is "locked" to this curated tool set (no terminal/file tools).
- **`access_hook/` — the gate.** An in-process Hermes `pre_tool_call` plugin that fails **closed** (Hermes is fail-open on hook errors, so the hook must enforce). It restricts tools by chat type: full trust in DMs, tasks-only in group chats. Container isolation — not this hook — is the real security floor.

**`esadmin` is separate** (operator surface, `scripts/everstone_cli.py`, Typer): `status`/`logs`/`restart`/`backup`/`sync-state`/`auth`/`model`/`session`/`setup`/`calendars`/`chat`. Don't conflate it with the agent's `es_*` tools.

**Caddy routing** (`config/caddy/Caddyfile`, served on `:80`): `/db/*`→CouchDB, `/caldav/*`→Radicale, `/hermes/*`→web UI (subpath on purpose — PWA service-worker scoping), `/oauth/google/callback`→the OAuth helper, `/version` and `/health` are direct responses; everything else 302s to `/hermes/`.

**Notes sync chain:** the agent writes to the vault at `/opt/data/vault` via `es_notes_*`; the **livesync-bridge** mirrors that directory ↔ CouchDB; Obsidian's Self-hosted LiveSync plugin syncs CouchDB ↔ devices. The bridge's storage peer must run with `useChokidar: true` + `scanOfflineChanges: true` (set in `generate_livesync_bridge_config`) — the native Deno watcher silently drops writes into subdirectories created after it starts (e.g. each day's `journal/YYYY-MM-DD/`). Full detail (chunk-tweak matching, remote-lock): `docs/architecture.md` → "Notes vault & LiveSync."

**Dev vs prod.** Dev runs inside an ephemeral **devm/SBX VM** (`devm.yaml`, committed — portable, secret-free); persistent state lives on a host mount that maps to the container's `/opt/data`. Prod is plain `docker run` on Unraid with bind mounts. Both: `config.yaml`→`/opt/config.yaml` (ro), data dir→`/opt/data`.

## Gotchas worth knowing before you change things

- **Agent skills are NOT in this repo.** They live in the profile data dir (`/opt/data/hermes/profiles/everstone/skills/<name>/SKILL.md`), are gitignored, and persist via the mount. Editing/syncing them is operator content, not a code change.
- **`SOUL.md` must be written into the profile dir** (`profiles/everstone/SOUL.md`); a global `$HERMES_HOME/SOUL.md` is shadowed by the profile's and ignored. `AGENTS.md` is found via `HERMES_TERMINAL_CWD` (`/opt/data`).
- **Hermes config contract is deliberately minimal** — EverStone asserts only `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS` (top-level key, not `messaging.telegram.allowed_users` — that one is dead), and `terminal.backend: local`; model/provider are a one-time `esadmin model`; everything else (display, reasoning, compression, auxiliary, telegram policy) is operator-owned. The full contract is `docs/hermes-integration.md`.
- **Google uses one shared OAuth credential** at `/opt/data/hermes/es/google-credentials.json` (scopes = union of enabled capabilities); the OAuth client must be **Web application** type with redirect `<public_url>/oauth/google/callback`. The flow is operator-run (`esadmin auth google`).
- **Cutover/env/dependency bugs surface only in live testing** (real Telegram message, OAuth round-trip), not unit tests — keep `e2e/` in mind for those.

## Design docs (the single source of truth)

Keep these authoritative and in sync with the code; this file points at them rather than duplicating:
- **`docs/architecture.md`** — design + rationale; the living source of truth for architecture.
- **`docs/hermes-integration.md`** — exactly what EverStone asserts in the Hermes config vs what's operator-owned.
- **`docs/BOOTSTRAP.md`** — deployment runbook (Unraid / `docker run` with bind mounts).
- **`README.md`** — short project overview.
