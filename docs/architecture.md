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
- **Web UI = hermes-webui** (`nesquena/hermes-webui`), an **opt-in** s6 service: a
  browser UI that runs the agent **in-process** under the agent venv. It is served
  by Caddy under **`/hermes/`** (binds `127.0.0.1:8787`; Caddy strips the `/hermes`
  prefix via `handle_path`, and the bare root and unmatched paths 302 to `/hermes/`),
  and **bypasses the Telegram allowlist + access_hook** (full agent tools), so it runs
  **only if `webui.password` is set** in `config.yaml` — unset → the service stays idle
  (no restart-loop) and `/hermes/` returns a "web UI not enabled" page. Reach it over
  Tailscale only. **It is mounted under a subpath (not root) on purpose:** the UI is a
  PWA, and at the root its service worker (scope `/`) intercepted root-level browser
  navigations — notably the OAuth callback `/oauth/google/callback` — and served its
  offline shell, so the auth code never reached the listener. Under `/hermes/` the SW
  scope is confined. No upstream patch is needed: hermes-webui is fully base-relative
  (`<base href>` derived from the URL, relative manifest/SW/fetches), so it works behind
  a prefix-stripping proxy. **Migration when cutting over an existing instance:** clear
  site data once per browser (DevTools → Application → Clear site data) to evict the old
  root-scoped service worker — a plain hard reload does not unregister it.
  Launched with `bootstrap.py --foreground` so s6 supervises `server.py` directly
  (without it, bootstrap double-forks and s6 restart-loops). `HERMES_WEBUI_AGENT_DIR=`
  the canonical checkout makes discovery work.

## Agent tool surface — the `es` MCP server

- **The agent's EverStone capabilities are exposed as MCP tools**, served by a
  **FastMCP** server in `es/es/mcp_server.py` (entry point `es-mcp`, registered in
  the Hermes profile under `mcp_servers: everstone-es`). Hermes spawns it as a
  subprocess and calls its tools. There is **no `es` CLI** — the agent is **"locked"**
  to this curated tool set (terminal/file toolsets are dropped), so the exposed tools
  *are* the capability boundary, not just a convention. Adding a capability = a new
  `@mcp.tool()` in the server backed by a thin client module.
- Tools, by capability:
  - **Tasks** — `es_tasks_*` (list/add/edit/done/delete/lists/list_create/
    list_delete/clear), backed by `es.tasks_client.TasksClient` (caldav, in-process).
  - **Calendar** — `es_cal_*` (agenda/search/conflicts/add/edit/delete) → **Google
    Calendar API directly** (`google-api-python-client`); gcalcli was dropped.
  - **Notes** — `es_notes_*` (journal/topic/topics/read/list) → the Obsidian vault
    via `es.vault_client.VaultClient` (see "Notes vault & LiveSync" below).
  - **Contacts / web** — `es_contacts_search` (read-only Google contacts) and a
    web-fetch tool (`trafilatura`).
  - **Maps** — reads via Google Maps Platform (`es_maps_geocode` / `search` / `place` /
    `directions` / `distance_matrix`), plus **Saved Places writes driven through the
    authenticated `google` browser** (`es_maps_star` / `unstar`), because no Google API
    writes Saved Places. Starring is what surfaces a place in Android Auto / Google
    Automotive. Discovery: `es_maps_lists` (what lists exist), `es_maps_list_places`
    (names in a list), `es_maps_place_lists` (which lists hold a place — the only EXACT
    membership test), `es_maps_resolve` (name → `[{place_id, address}]`).
    **`place_id` is the only key that acts; names are only for looking.** Saved-list rows
    carry no identifier of any kind, so `resolve` clicks through and recovers the id by
    searching Places for `"<name>, <address>"` and verifying the address matches — see
    `capabilities/maps_write.py`, which isolates every selector so a Google UI change has
    one place to fix and always degrades to `maps_automation_stale` + a deep-link fallback.
  - **Weather** — a single `es_weather(location, start, end)` → Google Maps Platform
    **Weather API**, reusing `maps.api_key`. Hourly endpoint **only**: its 240h horizon
    equals daily's, and daily can't share a shape with hourly observations (its
    day-parts carry no temperature), so one endpoint yields one uniform `Period`
    shape with no optional fields. `location` is required — there is no configured
    home. All times are local to the *forecast location*, never `timezone`.
- Every tool returns a **JSON envelope** (`{"ok": true, "data": …}` /
  `{"ok": false, "error": {code,message}}`). The server **reads `/opt/config.yaml`
  directly** (no envdir). `es_weather` additionally annotates `-> Envelope[T]`, which
  makes FastMCP publish a typed MCP **`outputSchema`** — the annotation must describe
  the *envelope*, not the payload, because `mcp_envelope` wraps the return and FastMCP
  validates the actual value against the published schema. This is the retrofit path
  for the other tools, which today publish no output schema at all.
- **Calendar times are event-local.** `_event_view` reports an event in its own zone (from `start.timeZone` — NOT the offset in `dateTime`, which Google renders in the calendar's zone)
  and adds `start_home`/`end_home` only when that differs from `timezone` — so a 3pm
  San Francisco meeting reads "3pm", not "5pm". `es_cal_add` takes wall-clock time at
  the *event's* location and its `tz` must be set for out-of-area events;
  `es_maps_geocode(query, include_timezone=True)` resolves a zone offline via
  `timezonefinder` (no second Google SKU, no extra call).
- **`es_tasks_*` is a full CalDAV task model** — flat lists with optional **one-level
  subtasks** (`RELATED-TO;RELTYPE=PARENT`: `add(parent=…)` files a child in the
  parent's list, `edit(parent=…)` re-parents/detaches, `delete` refuses a parent
  with children unless `force` cascades; completion is independent), `CATEGORIES`
  tags, `DUE`/`VALARM`, default list **`TODO`**. It is a **general mechanism** — no
  list is special-cased in code; all task *policy* lives in skills: **`todos`** (the
  `TODO` catch-all), **`shopping`** (🛒-prefixed persistent lists; clear-after-trip,
  never delete), **`checklists`** (ad-hoc; create→run-down→delete), and
  **`note-taking`** (journal-vs-topic routing for the vault).
- The capability server is kept **separate from the operator admin CLI `esadmin`**
  (`scripts/everstone_cli.py`, Typer): `status`/`logs`/`restart`/`backup`/
  `sync-state`; plus `auth` (google only — `auth hermes` was replaced by `model`)/
  `model`/`session`/`setup`/`calendars`/`chat`. `esadmin` is the **operator**
  surface; the `es_*` tools are the **agent** surface. Dev passthroughs:
  `just esadmin <args>` and `just model <value>` (= `esadmin model`). (The container
  name and the Hermes profile are both `everstone`.)
- **History:** EverStone was originally CLI-first (an `es` Typer CLI invoked through
  Hermes' terminal tool). It was migrated to **MCP-only** specifically to *lock* the
  agent to a curated surface — the fault-isolation that made the CLI-as-subprocess
  design attractive is preserved (the MCP server is still a separate process), while
  dropping terminal/file access makes the tool set the security boundary.

## Two integration surfaces — capability server (es) vs gate (access_hook)

- **`access_hook`** is an in-process Hermes **`pre_tool_call` plugin**. It gates
  tools by chat: DM = full trust; **groups = tasks only** (tool/argv check). It must
  be in-process (it intercepts). It **fails CLOSED** in our code because **Hermes is
  fail-OPEN on hook exceptions**. It is a *complete* chokepoint for the agent's tool
  calls (MCP, plugin, subagent, …). It is **not** the security floor — **container
  isolation** is.
- **`es-mcp`** runs as a **separate subprocess** (the MCP server Hermes spawns), so a
  hang/crash in a capability can't take down the gateway. The curated MCP tool set +
  the dropped terminal/file toolsets are what make the agent "locked"; `access_hook`
  then narrows that set further per chat type.

## Google auth

- **One shared Google credential** — JSON at
  `/opt/data/hermes/es/google-credentials.json`; scopes = **union of enabled
  Google capabilities** (today: Calendar). Adding a Google capability appends its
  scope → one re-consent.
- The **OAuth flow is operator-run**: `esadmin auth google` (Caddy-proxied
  callback, `scripts/auth_gcal.py`). `es` capabilities only *consume* the stored
  credential. `es` deps must include **`google-auth-oauthlib`** (the flow lib
  gcalcli used to provide).

## Notes vault & LiveSync

The `es_notes_*` tools write to an Obsidian vault on disk at `/opt/data/vault`
(journal entries under `journal/YYYY-MM-DD/`, curated docs under `topics/`). That
directory is the filesystem end of a three-link chain:

`es_notes_* → /opt/data/vault → livesync-bridge ⇄ CouchDB ⇄ Obsidian (Self-hosted LiveSync)`

- **livesync-bridge** (`vrtmrz/livesync-bridge`, an s6 service) mirrors the vault
  directory ⇄ the CouchDB `everstone` DB; its config is generated by
  `generate_livesync_bridge_config` in `scripts/configure.py`.
- The bridge's storage peer **must** run with `useChokidar: true` and
  `scanOfflineChanges: true`. The native Deno recursive watcher silently drops writes
  into subdirectories created *after* it starts watching — and es-notes makes new
  subdirs constantly (a fresh `journal/YYYY-MM-DD/` every day) — so chokidar handles
  new subdirs and the offline scan reconciles on every (re)start as a safety net.
  Without both, a note can land on disk yet never reach CouchDB/Obsidian.
- The bridge reads its chunk/E2EE tweaks (`customChunkSize`, `chunkSplitterVersion`,
  `handleFilenameCaseSensitive`, …) **only** from its own config — never the remote
  `tweak_values` — so they must match the plugin settings baked into
  `config/setup-obsidian-livesync`, or the bridge hashes/chunks content differently.
- Gotcha: LiveSync's **remote-lock** (set by a "Rebuild") admits only the rebuilding
  device to `accepted_nodes`; other devices stay blocked until they complete a Fetch.
  A stale lock can wedge a device even when data is converged — it's clearable on the
  milestone `_local` doc in CouchDB.

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
  `todos`, `shopping`, `checklists`, `note-taking`, `research`) live in the profile
  data dir (`$DATA_DIR/hermes/profiles/everstone/skills/<name>/SKILL.md`) —
  gitignored, persisted via the host mount, lost if the data dir is wiped. Shipping
  the core skills via the repo (e.g. a `skills/` dir installed at boot) for
  reproducibility is a future decision; for now they're operator content.
- Bake the `auxiliary.vision` config into `setup_hermes` (currently only live-set
  in the data dir).
