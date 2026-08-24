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
  - **Notes** — `es_notes_*` (journal/topic/topics/edit/attach/list) → the Obsidian
    vault via `es.vault_client.VaultClient` (see "Notes vault & LiveSync" below).
    Reading a note is no longer one of these tools — see `es_read` below.
  - **Documents** — `es_doc_extract(source, image_pages=None)` reads eight formats
    the agent otherwise has no way to open (no terminal, no OCR skill): `.pdf`,
    `.docx`, `.xlsx`, `.txt`, `.md`, `.csv`, `.json`, `.ics` — backed by
    `es.capabilities.docs` (confinement/caching/dispatch), one converter module
    per format (`doc_pdf.py`; `doc_office.py` for `.docx`/`.xlsx`; `doc_text.py` for
    the four flat formats; `doc_ics.py`), and `doc_cache.py` (the artifact cache).
    `image_pages` (PDF only) is an opt-in fallback that rasterizes specific pages
    into the SAME artifact — additive to the conversion, not a second document; see
    "Document reading & the doc cache" below.
  - **Read** — `es_read(target, section=None, query=None, offset=None)` is the one
    pageable read over both vault notes and `es_doc_extract`-converted documents.
    See "One read path: `es_read`" below for why this replaced `es_notes_read`
    rather than becoming yet another per-tool text branch.
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
    The session those writes ride on is seeded and kept alive as described in **"Authenticated
    browser & the `/web-login/` window"**; `maps_automation_stale` vs `authentication_required`
    is the split between "the UI moved" and "run `es_login`".
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

## Authenticated browser & the `/web-login/` window

Some capabilities have **no API at all** — Google Saved Places is the standing example — so
EverStone keeps a real, logged-in browser session and drives it. The login itself can only be
done by a human (password, 2FA, consent screens), so `es_login` **seeds** a session by putting
that browser on screen for the operator, once, and everything else keeps it alive.

- **Two isolated Camoufox instances, and the isolation is structural.** `camofox-flex` (:9377,
  root `/opt/camofox-flex`) serves the agent's flexible `browser_*` tools: **no plugins**, no
  pinned fingerprint, profiles under `/run/camofox-flex` **wiped at every start**.
  `camofox-auth` (:9378, root `/opt/camofox-browser`) owns the **authenticated** session:
  `fingerprint` + `vnc` + `persistence` plugins, profiles under `/opt/data/browser/profiles`.
  They need **separate install roots** because camofox resolves `camofox.config.json` from its
  own `ROOT_DIR` with no env override — a shared root would force a shared plugin set, and
  therefore a shared fingerprint and a reachable login. **`es` reads `CAMOFOX_AUTH_URL`**;
  pointing it at `CAMOFOX_URL` would silently drive the login-less instance. All of this is
  regression-guarded by `e2e/test_browser_isolation.py`, because a Dockerfile edit could
  re-merge the two and everything would still look healthy.
- **Liveness is a LIVE probe, never stored cookies.** `probe_home` browses google.com and reads
  the top-right affordance (account avatar vs a `ServiceLogin` link); ambiguous ⇒ signed out.
  The probe is also self-healing (it creates/restores the session, and the persistence plugin
  re-injects the durable login). `run_warm_keep` (s6 longrun, every 6h) browses so the
  short-lived `*PSIDTS` cookies keep rotating, gated on a cheap durable-cookie pre-check — and
  it **never** opens a login window; only a real tool use may do that.
- **The seeding surface is noVNC, mounted at `/web-login/`:**
  `browser → Caddy /web-login/* → websockify :6080 → x11vnc :5900 → Xvfb ← Camoufox renders`.
  The vnc plugin's watcher starts websockify **once** and then attaches — and **re-attaches** —
  x11vnc to whatever display Camoufox is currently using. websockify binds container-localhost
  and is never published, so **the Caddy block's mode is the entire access gate**.
- **That block is a three-state machine owned by `es`** (`web_login.py`), and it is **always
  present** in the Caddyfile so an idle or expired link never falls through to the catch-all
  (the Hermes UI password screen): **closed** (static "login isn't active — ask the assistant"),
  **preparing** (static, self-refreshing), **armed** (`reverse_proxy 127.0.0.1:6080`). Swaps
  match the block by its `handle_path` matcher, so formatting drift between the template and the
  swap can't break them. **GOTCHA:** the static pages must be **brace-free** — Caddy's `respond`
  treats `{…}` as placeholders — which is why "preparing" advances with a `meta refresh` rather
  than JS or CSS.

### Why a supervisor, not a one-shot arm

`_run_window` owns the route for the whole life of one login window. Each of its rules is a
failure that reached the operator as **"Failed to connect to server."** — a dead end, because
**noVNC never retries a FIRST connect** (`inhibitReconnect` starts `true` and is only cleared
in `connectFinished`), so the server must never publish a link into a gap. `reconnect=true` on
the URL only covers a drop **after** a successful connect, which is a different (real) case: the
watcher restarts x11vnc on any display change.

- **The browser launch happens inside the supervisor, retried** (~45s), never on `es_login`'s
  thread. camofox-auth binds its port seconds **after** the container reports healthy, and a cold
  Camoufox start can outrun an HTTP timeout; either way the old code raised *after* the route was
  already swapped to "preparing", leaving the operator refreshing a page that could never
  advance. A slow start now costs a few more seconds of "Preparing…", and `es_login` returns the
  link immediately instead of blocking on a browser launch.
- **Arming requires the WHOLE path, stable.** Readiness is a real WebSocket upgrade to websockify
  answered by an `RFB` banner — not a raw `:5900` accept, which can pass while websockify is
  wedged — held **continuously for ~3s on one display** (a display change resets the streak).
  x11vnc exits with its X server and is restarted by the watcher, so a single passing probe
  proves nothing.
- **The route keeps being checked after arming**, and after a few consecutive failures goes back
  to "preparing" (self-refreshing, so it re-arms if the path returns) and then to "closed" if it
  doesn't. The invariant is that **`/web-login/` never serves noVNC that a fresh client can't
  connect to** — a one-shot arm left a dead pipe exposed for the rest of the window.
- **The login tab is beaten every 30s.** camofox reaps by *API* activity, and VNC keystrokes
  never touch its API, so a hand login looks abandoned from the moment it opens: tab reaped →
  empty session closed → browser idle-shutdown → Xvfb and x11vnc gone, with the route still
  armed. Measured with shortened reapers: armed at 7s, dead at 155s. `GET /tabs/<id>/downloads`
  is the cheapest call that bumps both counters the reapers read and touches nothing on the page.
- A **generation guard** means a newer `open_signin`/`close_window` supersedes every background
  worker from an older call, and a fail-safe timer closes the window after 10 minutes if the
  operator never returns.

**GOTCHA — the noVNC `path` param must be ABSOLUTE.** noVNC resolves it with
`new URL(path, location.href)` against `/web-login/vnc.html`, so a relative `web-login/websockify`
becomes `/web-login/web-login/websockify`. That still works here *by luck* — `handle_path` strips
one segment and websockify upgrades a WebSocket on any path — but behind a stricter proxy it
fails **100% of the time, immediately**, while every server-side check looks perfect. No unit
test can catch this: it lives in the URL the client builds for itself. `e2e/test_web_login_vnc.py`
therefore drives an actual browser (via the flex instance) at the real page and asserts both the
resolved WS path and that it reaches a desktop, alongside hand-rolled WS clients that assert the
route's invariant continuously through the open→armed→dead lifecycle.

## Notes vault & LiveSync

The `es_notes_*` tools write to an Obsidian vault on disk at `/opt/data/vault`: journal entries
under `Journal/YYYY-MM-DD/`, curated docs under **category** folders — `obsidian.categories` in
`config.yaml`, defaulting to `[Topics]`, with `obsidian.journal_folder` defaulting to `Journal`.
Categories are top-level folders the agent may file into (e.g. `Recipes`, `People`, `Places`);
folder names are **case-sensitive** on disk, so a lowercase `topics/` left over from an older
vault is simply invisible to `es`. That directory is the filesystem end of a three-link chain:

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

## Document reading & the doc cache

`es_doc_extract` (`es/es/capabilities/docs.py`) lets the agent read a file the user sent, since
the agent has no terminal and no OCR skill to fall back on (Hermes's own attachment note suggests
both — neither exists in this profile; the tool is the actual path).
Eight formats are supported today — `.pdf`, `.docx`, `.xlsx`, `.txt`, `.md`, `.csv`, `.json`,
`.ics` — dispatched from a single table, `docs.CONVERTERS`, keyed by lowercased extension;
`SUPPORTED` is *derived* from that table rather than hand-maintained beside it, so a format can
never claim to be supported without a converter actually behind it. Adding a ninth format is
meant to be a pure addition — one new converter module plus one new table entry — never a
change to `extract()` itself.

- **`extract()` returns a receipt, not the document.** The full return shape is `{doc_id, kind,
  page_count, preview, complete, page_images, next}` — never the converted Markdown itself. `preview` is the
  first ~800 characters (`docs.PREVIEW_CHARS`), sized to let the agent identify what it's holding
  (a title, a first paragraph, a table header), not to read it; `complete` is an exact test
  (`len(markdown) <= PREVIEW_CHARS`), so the agent can trust it rather than guess from the
  preview's own shape. The cut itself never lands mid-token: a raw `markdown[:PREVIEW_CHARS]`
  slice can end inside a single-line `![page N](path)` image link (every scanned PDF page emits
  one), handing back a truncated, unusable path — the cut instead falls back to the last line
  boundary at or before the limit (`doc_support.rfind_safe_cut`, also used by `es_read`'s own
  content cap below, so the two don't grow independent copies of the same rfind). `doc.md` still
  caches the **entire** converted document regardless of preview size; `es_read` (below), given
  the `next`-supplied `doc:<doc_id>` handle, pages that full cache by heading — the preview cap
  bounds only what one `es_doc_extract` call returns, never what the agent can eventually read.
- **Output is Markdown, not raw text — and every converter emits a `## ` heading everywhere the
  SOURCE has real structure.** This is the load-bearing design choice the rest of this section
  depends on. `doc_pdf` emits `## Page N` per page; `doc_office` emits one heading per Word
  heading (`.docx`) or one per worksheet (`.xlsx`, emitted UNCONDITIONALLY even for an empty
  sheet — a sheet silently dropped for having nothing under its own heading would be
  indistinguishable from a sheet that was never read); `doc_ics` synthesizes one heading per
  calendar EVENT, deliberately not per day — grouping by day is a property of how someone
  chose to *display* a schedule, not of the schedule itself, and would make the reader's paging
  granularity depend on an accident of formatting rather than the feed's own structure. Formats
  with no real structure — `.txt`, `.md` (as authored), `.csv`, `.json`, all served by
  `doc_text.py` — deliberately emit NONE: inventing a heading (e.g. `"## Rows 1-100"`) over an
  arbitrary byte range would counterfeit the one signal a reader is meant to trust ("a `##`
  heading marks a real section"). This is *why* `es_read` (below) can page any converted
  document by heading with no format-specific logic of its own — the format-specific work
  already happened once, inside the converter, at conversion time.
- `doc_pdf.convert()` (pdfplumber for text/tables, pypdfium2 for rendering) walks pages in order
  and extracts, unconditionally, every source of visual content a page has: every embedded raster
  image (no "is this worth it" threshold — a fully scanned page falls out of this for free, as a
  page whose only content happens to be one image) and every vector drawing (a chart/diagram drawn
  in lines/rects/curves, clustered by proximity, table borders subtracted first so a bordered
  table's own grid never doubles as a spurious "drawing"). Both are linked inline at their
  position in the reading order, `![page N image M](path)`/`![page N drawing M](path)`, read with
  `vision_analyze` — no separate escalation protocol needed, and no judgment call left for the
  agent to notice and act on. `.docx`'s own embedded images are extracted the same way (linked
  inline in document order); `.xlsx` embedded images are still out of scope. Each of the three
  resources (images, drawings, `.docx` images) has its own document-wide ceiling, hit only by a
  pathological/crafted file and always reported in-band, never silently dropped.
- **`es_doc_extract`'s `image_pages` parameter is a fallback for unreadable TEXT, not images** —
  by the point above, every image on a page is already inline, so there is nothing left for a
  whole-page render to reveal there. What it *does* serve: a page whose extracted TEXT itself
  comes back unusable — pdfplumber's line grouping has no column awareness, so a real multi-column
  PDF (verified against `tests/fixtures/pdf/example_paper.pdf`) can interleave two columns'
  sentences mid-clause; a mangled table/form layout is the same failure. `image_pages` (e.g. `"7"`,
  `"1-5"`) rasterizes exactly those pages **into the SAME artifact** the plain conversion already
  produced — additive, not a second document: `doc_id` is a pure content-and-format hash and does
  not fold in whether/which pages were rendered, so a later call with `image_pages` against an
  already-converted source is a conversion cache hit that only does the extra rendering work. It
  is **PDF-only**: every other format has no `render()` to dispatch to (`hasattr(mod, "render")` is
  the capability check — the same "presence of the optional attribute IS the capability"
  convention `page_count` uses to mean "this format has pages at all"), so `.docx`/`.xlsx` and the
  four flat formats refuse it by name rather than failing confusingly deeper in page-range logic.
  The receipt's `page_images` key is always present (`[]` when `image_pages` wasn't given).
- **Artifacts live in `.es/<doc_id>/` *inside* Hermes's own per-profile document cache**
  (`cache/documents/` under the profile dir) rather than a separate directory — `doc_cache.py`
  namespaces under it instead of duplicating it. This is safe specifically because Hermes's own
  `_cleanup_cache_dir` iterates cache files only and never recurses into subdirectories, so it
  will never see (or purge) anything under `.es/`; conversely, `es`'s own purge only ever walks
  its `.es/` namespace, never Hermes's inbound files beside it. `doc_id` is a hash of the file's
  content **and its format** (`ext.encode() + b"\0" + <file bytes>`), not content alone: the same
  bytes read as two different formats (a PDF once mistakenly uploaded/extracted as `.csv`) are
  two different, independently-correct documents — different `kind`, different markdown,
  different `page_count` — so folding only the content into the hash would let whichever format
  got extracted first silently poison the cache for every later request of the *other* format,
  for the full TTL.
- **The cache TTL is 24h since last ACCESS, not creation.** `doc_cache.touch()` bumps an
  artifact directory's mtime on every cache hit (extract, render, and now every `es_read` of a
  `doc:<id>` target too — see below), and `purge()` evicts only directories whose mtime is older
  than the TTL. A creation-based clock would delete a document out from under a conversation
  that's still actively reading it (asking follow-up questions about page 12 an hour after the
  first extract); measuring last use instead means a document only expires once nobody has
  touched it for a full day.
- **Path confinement is the actual security boundary here, not `access_hook`.** `source` is an
  agent-supplied string; `paths.resolve_readable` confines it to `config.readable_source_dirs()`
  — the attachment source dirs (where Telegram uploads land) plus the vault root — resolving
  symlinks and checking containment *before* even checking existence, so the tool cannot be used
  as a path-existence oracle outside those roots. That check matters because `access_hook`
  grants full tool trust in DMs; without confinement in `docs.py`/`paths.py`, a DM user could ask
  the agent to read arbitrary files elsewhere on the container's filesystem.

### One read path: `es_read`

`es_read(target, section=None, query=None, offset=None)` (`es/es/capabilities/reader.py` +
`es/es/capabilities/read.py`) replaced `es_notes_read` as the **only** way the agent reads a
vault note, and is *also* the only way it reads a document previously converted by
`es_doc_extract`. `target` is a vault-relative path, a topic name (the same convention
`es_notes_journal`/`es_notes_topic` already use), or a `doc:<id>` handle returned by
`es_doc_extract`. `reader.py` resolves whichever form `target` takes down to one Markdown
string plus a `kind` (`"note"` or `"doc"`), and hands that string to `read.py`'s pure,
format-agnostic primitives: `outline` (every ATX heading `#` through `######`, in order — a
converter's `## Page N` and a note's own `# Title`/`### Sub` are all entries), `section` (one
heading's body, its own subsections included), `window` (page by raw line, for structure-free
content like a `.csv` that has no headings at all), and `query` (case-insensitive full-text
search). `query` returns *matching outline entries* — the id a follow-up `section` call needs,
not raw snippets — except for content with no headings at all, where there is no section to
name and it returns `{offset, line}` hits the agent pages to with `offset` instead.

A short target comes back whole (`more=false`); a long one comes back as an outline, plus the
preamble or, when the document opens straight onto a heading, its first section — and
`more=true`. "Long" is a much higher bar for a note (16,000 characters — authored by a person,
naturally bounded, and usually better served by an answer than an outline) than for a document
(4,000 characters — a converted document arrives at whatever size its source format happened to
be, and the threshold has to stay tight enough that a season's calendar feed crosses it rather
than sliding through as "small enough to return whole"; the real 117-event feed measures ~8,200
characters, so 4,000 catches it with room to spare).

**Whatever mode is taken, `content` itself is capped at 32,000 characters** with an in-band
marker, and that cap is not decoration. Hermes spills any MCP result past
`DEFAULT_MCP_RESULT_SIZE_CHARS` (50,000) to a file, leaving only a preview in context — and
this agent has no file tools, so it cannot open the spillover. An uncapped read of a large note
would hand the agent a preview of content it is structurally unable to retrieve. 32,000 rather
than the full 50,000 because `outline` shares the envelope, and a 117-entry outline is itself
several thousand characters.

**Why one read path — not a reader grown per capability — is the load-bearing decision here.**
Before `es_read` existed, `es_web_fetch` grew its own inline branch for returning long text-ish
bodies (calendar feeds, JSON/XML, plain text) verbatim, truncated at 40,000 characters with a
marker stating plainly that there was no way to fetch the rest — a second, ad hoc pageable-ish
reader, built to fill exactly the gap `es_read` was already designed to fill. It was built,
reviewed, merged, and then deleted (`702d339`) once the duplication was recognised: the right
fix for "this tool needs to hand back long content" is *always* "read it through `es_read`",
never a bespoke truncate-and-hope branch bolted onto whichever tool needed it that day. Nobody
should re-add it. `es_web_fetch` today stays deliberately thin — extracted prose for
`text/html`, `thin=true` plus a `note` for everything else, no `cached_path`/`doc`/`truncated`
fields at all — precisely so that a future "fetch a document from the web and read it properly"
need routes through `es_doc_extract`/`es_read`, not through a second copy of the pagination
logic this section just described. If a change ever adds truncation-with-a-marker to some new
tool's own return value, that is the signal to stop and reconsider: `es_read` is where paging
lives now.

### `es_web_fetch`'s address guard

`es_web_fetch` returns response bodies (extracted text, or at minimum a content-type) to the
agent, which makes it a read primitive for anything reachable from the container — not just
public sites — once any body can come back (before text passthrough existed, a non-HTML body
was simply discarded, which made this inert). `url_guard.check_url()` (`es/es/url_guard.py`)
resolves the target host and inspects the ADDRESSES it resolves to, not the string — a
public-looking name whose A record is `127.0.0.1` is blocked just the same. It blocks: loopback
(v4 + v6), IPv4 link-local (which covers cloud metadata, `169.254.0.0/16`) and its IPv6
equivalent, RFC1918 (`10/8`, `172.16/12`, `192.168/16`), CGNAT (`100.64.0.0/10` — also
Tailscale's own range), unique-local IPv6, NAT64, and IPv4-mapped/6to4/Teredo IPv6 literals —
those last three encode an IPv4 address inside an IPv6 wrapper, and `::ffff:127.0.0.1` verifiably
bypassed an earlier version of this guard for exactly that reason (checking the IPv6 form
against IPv4 ranges never matches unless it's unwrapped first). It fails **closed**: a host
that won't resolve, resolves to nothing, or resolves to an address that can't be parsed is
refused rather than attempted. `_http_get` calls `check_url` explicitly on the initial request
*and* registers it as an httpx **request** event hook — which httpx fires once per hop,
including every redirect it follows — so a public URL that redirects to an
internal one is caught at the hop, not just up front — the explicit call means the refusal
isn't hook-dependent even before the first request goes out.

The blocked-ranges list is a deliberate, hand-picked enumeration, **not** Python's broader
`ipaddress.is_private`/`is_reserved` predicates. Those predicates also cover the IANA
documentation/TEST-NET ranges (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and the
benchmark range (`198.18.0.0/15`) — ranges where nothing listens, so blocking them buys no
security anywhere. That distinction is not academic here: the devm dev VM's (and the sandbox's)
transparent-egress DNS resolves **every** public hostname to `192.0.2.1`, a TEST-NET-1 address —
a guard built on `is_private` would block every real fetch in dev while protecting nothing
extra in prod. Not covered yet: `es_web_fetch` doesn't cache what it fetches the way
`es_doc_extract` does — a repeat fetch of the same URL is a fresh request, not a cache hit, and
there is no `doc:<id>` handle to hand a fetched page over to `es_read`.

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
