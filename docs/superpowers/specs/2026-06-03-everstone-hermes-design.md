# EverStone v2 — Hermes-Centered Personal Hub

- **Date:** 2026-06-03
- **Status:** Design — awaiting review
- **Supersedes:** the bidirectional markdown↔CalDAV bridge in `building-blocks.md`

## 1. Vision

EverStone becomes a single self-hosted container that hosts a person's notes,
tasks, and backups, with an AI agent (**Hermes Agent**) sitting on top of both
notes and tasks as a persistent personal assistant reached from a phone via
Telegram.

- **Notes** live in Obsidian, synced through CouchDB (LiveSync), and are exposed
  to Hermes as **plaintext files** via `livesync-bridge`.
- **Tasks** live in Radicale (CalDAV), authored/managed in a native Mac CalDAV
  client and Tasks.org on the phone, and reached by Hermes through a small
  `everstone-tasks` CLI.
- **Tasks link to notes** via `obsidian://` deeplinks that Hermes maintains.
- **Backups** continue via the existing git HTTP backend.

The marquee change from the original project: we are **not** mirroring markdown
checkboxes into VTODOs. Tasks and notes are independent stores; the only
connection is a deeplink, and the intelligence that ties them together is
Hermes, not a hand-written sync engine.

## 2. Background — what changed and why

The original design (`building-blocks.md`) tried to parse markdown tasks,
generate stable UIDs, mirror them into CalDAV VTODOs, watch both sides, and
reconcile bidirectional edits. That is the hardest, most fragile part of the
whole concept (UID stability across edits, conflict resolution, two sources of
truth for the same text).

The pivot:

1. Tasks are managed in a real CalDAV client, not inside Obsidian.
2. The note↔task relationship is a **link**, not a content copy.
3. **Hermes Agent** is added as the brain that reads/writes both stores.

Consequences:

- The custom event-emitting Radicale storage (`radfire`) is **removed**. It only
  ever bought *real-time reaction* to task changes, which none of the planned
  jobs require (they are message-triggered, cron-triggered, or on-demand). If
  real-time is ever wanted, Radicale's built-in `[storage] hook` can run a
  one-line "poke Hermes" command — no custom storage class needed.
- The notes-access problem (LiveSync stores E2E-encrypted, path-obfuscated
  chunks in CouchDB) is solved by `livesync-bridge`, which holds the passphrase
  and materializes a plaintext folder.
- The unbuilt `taskite` SvelteKit scaffold is **removed** — interaction happens
  through Telegram, so a web dashboard is redundant.

## 3. Architecture

Single container, s6-overlay supervising all long-lived services.

```
Obsidian (all devices) ─┐
                        ├─⇄ CouchDB (/db) ⇄ livesync-bridge ⇄ [ /opt/data/vault ]
Mac CalDAV client ──────┐                                            ▲
Tasks.org (phone) ──────┴─⇄ Radicale (/caldav)  ◄────────────┐      │ file tools
                                                             │      │
                                       everstone-tasks CLI ──┴──── Hermes Agent
                                                              (Telegram gateway, cron, skills)
```

**Services under s6:** `caddy · couchdb · radicale (plain) · git/fcgiwrap ·
livesync-bridge (Deno) · hermes`.

**Caddy routing (unchanged):** `/db → couchdb`, `/caldav → radicale`,
`/git → git-http-backend`, `/health`, `/* → "EverStone Server"`.

**Data layout on the `/opt/data` volume** (everything that must survive restarts):

| Path | Owner | Purpose |
|------|-------|---------|
| `couchdb/` | couchdb | CouchDB data (existing) |
| `git/` | git | bare backup repo (existing) |
| `radicale/collections/` | radicale | VTODO `.ics` storage |
| `radicale/htpasswd` | radicale | CalDAV auth |
| `vault/` | hermes | plaintext vault (livesync-bridge `baseDir`) |
| `hermes/` | hermes | `~/.hermes`: OAuth token, memory, skills, sessions |

## 4. Components

### 4.1 Radicale (replaces radfire)

Stock Radicale run as an s6 `longrun`. `multifilesystem` storage at
`/opt/data/radicale/collections`; htpasswd auth at `/opt/data/radicale/htpasswd`.
Config templated by `configure.py` from `config.yaml`. No custom Python package;
the `radfire/` directory and its event/consumer/storage modules are deleted.

- **What it does:** serves CalDAV VTODOs to the Mac client, the phone, and the
  `everstone-tasks` CLI.
- **Depends on:** the data volume, htpasswd creds from config.

### 4.2 livesync-bridge (Deno)

`vrtmrz/livesync-bridge` run as an s6 `longrun`. Bidirectionally replicates the
CouchDB LiveSync database to/from `/opt/data/vault` as plaintext, decrypting with
the LiveSync E2E passphrase and obfuscation passphrase.

- **What it does:** gives Hermes plaintext notes to read/write with native file
  tools; pushes Hermes's edits back to CouchDB → every Obsidian device.
- **Config:** `configure.py` templates `dat/config.json` with two peers — a
  `couchdb` peer (`http://localhost:5984`, database name, username/password,
  `passphrase`, `obfuscatePassphrase`) and a `storage` peer
  (`baseDir: /opt/data/vault`).
- **Depends on:** Deno runtime (now permanent in the image), CouchDB, the
  passphrase secret.

### 4.3 Hermes Agent

Installed via `pip install hermes-agent`; run as an s6 `longrun` daemon with the
Telegram gateway enabled.

- **Model auth:** ChatGPT subscription via **Codex OAuth** (`hermes auth add
  codex-oauth`), `gpt-5-codex` family. No API key, no per-token billing.
- **`terminal.backend = local`** — code exec happens in the container (no
  Docker-in-Docker). Acceptable for a personal box.
- **Persistence:** `HOME` is set so `~/.hermes` resolves under `/opt/data/hermes`
  — OAuth token (auto-rotating), memory, skills, and session history all survive
  restarts.
- **Config:** `configure.py` templates Hermes's `config.yaml` (provider/model
  defaults, gateway = telegram); the Telegram bot token is a secret from
  `config.yaml` → Hermes's `.env`.
- **Depends on:** outbound HTTPS to OpenAI, the `/opt/data/hermes` volume, the
  `everstone-tasks` CLI, and the `/opt/data/vault` folder.

### 4.4 everstone-tasks CLI

A small purpose-built Python CLI (using the `caldav` library), installed into the
image and invoked by Hermes via its shell tool. Talks to Radicale on
`http://localhost:5232` with the CalDAV creds.

- **Commands (all `--json` capable):**
  - `list [--list NAME]` — tasks with uid, summary, status, due, url(deeplink).
  - `add SUMMARY [--list NAME] [--due ...] [--note VAULT_PATH]` — create a VTODO;
    if `--note` given, set its `URL` to the note's `obsidian://` deeplink.
  - `done UID` / `update UID [fields]` — mutate a task.
  - `link UID --note VAULT_PATH` — set/replace the deeplink on an existing task.
- **Why custom:** first-class JSON for the agent to parse, and first-class
  support for the deeplink URL field — cleaner than wrapping `todoman`/`vdirsyncer`.
- **Depends on:** Radicale, `caldav` lib, the vault name (for deeplink building).

### 4.5 configure.py (extended)

Continues to merge `config.yaml` + defaults, validate against the JSON schema,
and template service configs. **New outputs:** Radicale config + htpasswd,
livesync-bridge `dat/config.json`, and Hermes `config.yaml`/`.env`. Existing
outputs (Caddyfile, CouchDB `local.ini`, `setupuri`) are unchanged.

## 5. Configuration & secrets

`config.yaml` / `schema.json` gain:

```yaml
couchdb:            # existing
  user: ...
  password: ...
  database: ...
git:                # existing
  user: ...
  password: ...
caldav:             # NEW — Radicale auth used by clients + the CLI
  user: ...
  password: ...
livesync:           # NEW — for livesync-bridge
  passphrase: ...            # E2E encryption passphrase
  obfuscate_passphrase: ...  # path-obfuscation passphrase (often same value)
obsidian:           # NEW — for deeplink construction
  vault_name: ...
hermes:             # NEW
  model: openai-codex        # provider/model defaults
  telegram_bot_token: ...    # secret → Hermes .env
```

All new secrets are validated and templated the same way existing ones are. The
**LiveSync passphrase is the most sensitive new input** and must exactly match
the vault's existing encryption settings, or sync will fail/corrupt.

## 6. Deeplink convention

`task.URL = obsidian://open?vault=<vault_name>&file=<url-encoded vault path>`

- Hermes (and the CLI's `--note`/`link`) stamp this into the VTODO `URL`
  property. Most CalDAV task clients render `URL` as a tappable link, so
  completing/opening a task can jump straight to its backing note.
- Direction is **task → note** for v2. Note → task linking is out of scope (see §11).

## 7. The four jobs (ride on top — described, not core build)

Once the plumbing works, the requested capabilities are largely Hermes
configuration (skills + cron), not bespoke code:

- **Capture & triage:** Telegram message → Hermes creates a note (file write to
  `vault/`) and/or a task (`everstone-tasks add`), files it, sets due dates,
  stamps a deeplink.
- **Proactive briefings:** natural-language **cron** → Hermes reads current task
  state + relevant notes and pushes a summary to Telegram. Paced to respect
  subscription rate limits.
- **Q&A over the vault:** Hermes searches `vault/` with its file tools and
  answers, surfacing related notes.
- **Organize & enrich:** periodic sweep — maintain deeplinks, tag, dedupe,
  restructure. (This is the only job that would have benefited from real-time
  task events; a sweep covers it.)

These are validated manually after the plumbing lands; they are not unit-tested
code.

## 8. Bootstrap / setup flow

1. Fill `config.yaml` (couchdb, git, caldav, livesync passphrase, obsidian vault
   name, hermes telegram token).
2. `docker run` (or `run_local.sh`).
3. `docker exec -it everstone hermes auth add codex-oauth` → complete the
   device-code login in a browser (once; token then auto-rotates on the volume).
4. `docker exec -it everstone hermes gateway setup` → Telegram (paste bot token
   if not already in config).
5. Point clients at the server: Obsidian via `setupuri`, the Mac CalDAV client
   and the phone at `/caldav`.

## 9. Removed

- **radfire** — the entire custom Radicale storage package (`storage.py`,
  `events.py`, `consumer.py`, `server.py`, `pyproject.toml`) and its Dockerfile
  install step. Replaced by stock Radicale.
- **taskite** — the unbuilt SvelteKit scaffold and its tooling.

## 10. Testing strategy

Chosen level: **protocol-level e2e + a documented manual smoke test.** Driving the
real Obsidian GUI in CI (xvfb/Electron) is explicitly rejected as too brittle.

- **everstone-tasks:** unit tests for command behavior and deeplink construction,
  run against an ephemeral Radicale (or a mocked `caldav` client).
- **configure.py:** tests that rendering produces valid Radicale config,
  livesync-bridge `config.json`, and Hermes config from a sample `config.yaml`;
  schema-validation failure cases.
- **Protocol-level e2e (automated CI):**
  - **Notes round-trip:** run CouchDB + **two `livesync-bridge` instances**
    (folder A ↔ CouchDB ↔ folder B) with encryption + obfuscation on. Assert a
    file created/edited/deleted in folder A propagates to folder B (and is a
    valid LiveSync doc in CouchDB). A second bridge is a faithful stand-in for
    "another Obsidian device," since it is a LiveSync-compatible client by the
    same author.
  - **Tasks round-trip:** real Radicale + `everstone-tasks` + a CalDAV client
    lib; assert add/done/link and that the `obsidian://` deeplink survives a
    write→read cycle.
- **Container smoke test:** build the image, boot it, assert `/health` is 200 and
  each s6 service reaches "up."
- **Manual smoke (documented, one-time):** one real Obsidian device against a
  **throwaway vault** to confirm the true GUI round-trip before pointing the
  stack at the real vault. The real Obsidian GUI itself is not automated.

## 11. Out of scope (YAGNI for v2)

- Markdown↔VTODO content sync (explicitly retired).
- Real-time task-change reaction (recoverable later via Radicale `[storage] hook`).
- Note → task deeplinks (task → note only for now).
- A web UI / status dashboard.
- Multi-user; this is a single-person hub.
- **Home Assistant integration (future):** a likely next interaction point. It
  rides on Hermes (which already supports Home Assistant as a gateway, and can
  call HA's REST/websocket API via its own tools/MCP), so it needs no EverStone
  plumbing and is deferred — not planned in v2.

> Note: Obsidian shipped an official **headless sync client** (npm, Feb 2026),
> but it targets Obsidian's paid **Sync** service, not the self-hosted
> CouchDB/LiveSync backend EverStone uses — so it does not apply here.

## 12. Risks & open questions

- **LiveSync passphrase correctness:** a mismatch with the existing vault's
  settings can corrupt sync. Mitigation: validate against a throwaway vault first.
- **Codex OAuth in a headless container:** login is interactive once via
  `docker exec`; confirm device-code flow works without a local browser on the
  host (it should — it prints a URL + code).
- **Subscription rate limits:** unattended cron sweeps can hit ChatGPT/Codex
  caps. Mitigation: conservative cron cadence; Hermes stays model-agnostic if a
  fallback provider is ever needed.
- **Agent writes vs. live Obsidian edits:** concurrent edits to the same note;
  LiveSync handles conflicts, but Hermes should prefer append/section edits over
  wholesale rewrites.
- **`local` terminal backend:** Hermes can run arbitrary commands in the
  container. Acceptable for a personal box; note it as a security posture choice.
- **Relocating `~/.hermes`:** confirm the exact mechanism (HOME vs. a config key)
  during planning.
- **Image weight:** adds Deno + Node + Hermes; acceptable but larger.
