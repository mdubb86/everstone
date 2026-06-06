# es Cutover — Implementation Plan (Plan 3 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the running system over to `es` — gate it in group chats, generalize Google auth, wire it into the image and config rendering, point AGENTS.md + the calendar skill at it, and retire `gcal` / `everstone-tasks` / gcalcli.

**Architecture:** Edits to existing files only (no new runtime components). The new `es` package (Plans 1–2) gets installed in the image; the access_hook gates `es tasks` in groups; the operator OAuth flow generalizes to all-Google scopes and a relocated JSON credential store; tool env vars are retired since `es` reads `config.yaml` directly.

**Tech Stack:** s6, Docker, Python (Typer/google-auth), the EverStone `configure.py` render pipeline.

**Prereqs:** Plans 1 & 2 merged (`es` package exists, installs an `es` console script, `es.google_auth` + `es cal` + `es tasks` present and green).

> **EXECUTOR NOTE:** This plan edits live files whose exact line numbers may shift as Plans 1–2 land. Each task gives the anchor text to find, not just line numbers. Verify each anchor before editing.

---

### Task 1: Gate `es tasks` in groups + harden the access_hook

**Files:**
- Modify: `access_hook/everstone_access_hook.py`
- Modify/Test: `access_hook/tests/test_access_hook.py`

- [ ] **Step 1: Write the failing tests** (add to `access_hook/tests/test_access_hook.py`)

```python
import os
from everstone_access_hook import policy


def _group(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", "agent:main:telegram:group:123")


def test_group_allows_es_tasks(monkeypatch):
    _group(monkeypatch)
    assert policy("terminal", {"command": "es tasks list --list inbox"}) is None


def test_group_blocks_es_cal(monkeypatch):
    _group(monkeypatch)
    assert policy("terminal", {"command": "es cal agenda 2026-06-08 2026-06-09 --calendar Family"}) == \
        {"action": "block", "message": "Tool not permitted outside a private DM."}


def test_group_blocks_bare_es(monkeypatch):
    _group(monkeypatch)
    assert policy("terminal", {"command": "es"})["action"] == "block"


def test_hook_never_raises_on_bad_input(monkeypatch):
    _group(monkeypatch)
    # tool_input not a dict, weird types — must return a dict, never raise
    assert policy("terminal", 12345)["action"] == "block"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd access_hook && python -m pytest tests/test_access_hook.py -k "es_tasks or es_cal or bare_es or never_raises" -v`
Expected: FAIL (group currently allows `everstone-tasks`, not `es tasks`).

- [ ] **Step 3: Update the policy to gate on `argv[0]=="es"` AND `argv[1]=="tasks"`, and wrap to never raise**

In `access_hook/everstone_access_hook.py`, replace `_group_allowed_binaries` + the argv check in `policy`:
```python
# In groups, only `es tasks ...` is allowed. (Was {everstone-tasks} pre-es.)
_GROUP_ALLOWED = ("es", "tasks")  # (argv[0], argv[1])


def _es_tasks_invocation(command: str) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return len(argv) >= 2 and argv[0] == _GROUP_ALLOWED[0] and argv[1] == _GROUP_ALLOWED[1]
```
Replace the tail of `policy` (the argv0 block) with:
```python
    if not _es_tasks_invocation(command):
        return _BLOCK
    return None
```
Then make the entry point fail-closed against ANY internal error (Hermes is
fail-OPEN on hook exceptions). Replace `HermesPlugin.pre_tool_call`:
```python
class HermesPlugin:
    def pre_tool_call(self, tool_name, **kwargs):
        try:
            return policy(tool_name, kwargs.get("args") or kwargs.get("tool_input"))
        except Exception:
            # Hermes is fail-OPEN on hook exceptions; we fail CLOSED in groups.
            return None if _chat_type() == "dm" else _BLOCK
```
(Delete the now-unused `_group_allowed_binaries` and `_extract_argv0` if no longer referenced; keep `_GROUP_FORBIDDEN_SUBSTRINGS` composition check before the invocation check.)

- [ ] **Step 4: Run to verify pass**

Run: `cd access_hook && python -m pytest tests/test_access_hook.py -v`
Expected: PASS (all, including pre-existing DM-allow + composition-block tests).

- [ ] **Step 5: Commit**

```bash
git add access_hook/everstone_access_hook.py access_hook/tests/test_access_hook.py
git commit -m "feat(access-hook): gate es tasks in groups; fail-closed on hook errors"
```

---

### Task 2: Generalize Google auth (union scopes) + relocate creds to a JSON store

**Files:**
- Modify: `es/es/google_auth.py` (read JSON store; shared scopes list)
- Modify: `scripts/auth_gcal.py` (request scope union; write JSON via `to_json`)
- Modify: `scripts/everstone_cli.py` (`auth google` command)
- Test: `es/tests/test_google_auth.py` (JSON path)

- [ ] **Step 1: Update the failing test for the JSON store**

Replace the pickle-based tests in `es/tests/test_google_auth.py` with:
```python
from unittest.mock import MagicMock, patch
from es import google_auth


def test_load_credentials_reads_json_and_refreshes(tmp_path, monkeypatch):
    p = tmp_path / "google-credentials.json"
    p.write_text("{}")
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(p))
    creds = MagicMock(); creds.valid = False
    with patch("es.google_auth.Credentials.from_authorized_user_file", return_value=creds), \
         patch("es.google_auth.Request"):
        out = google_auth.load_credentials()
    creds.refresh.assert_called_once()
    assert out is creds


def test_scopes_is_calendar_only_for_now():
    assert google_auth.GOOGLE_SCOPES == ["https://www.googleapis.com/auth/calendar"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd es && python -m pytest tests/test_google_auth.py -v`
Expected: FAIL (still pickle-based / no `GOOGLE_SCOPES`).

- [ ] **Step 3: Rewrite `es/es/google_auth.py` to JSON + shared scopes**

```python
"""Shared Google credential consumer for es. Reads the JSON credential store,
refreshes if expired, builds API services. The OAuth flow is operator-run
(scripts/auth_gcal.py); es only consumes."""
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Union of scopes for all enabled Google capabilities. Append here when adding
# a new Google capability (e.g. gmail.readonly for es mail) — then re-consent.
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_DEFAULT_CREDS_PATH = "/opt/data/hermes/es/google-credentials.json"


def _creds_path() -> Path:
    return Path(os.environ.get("ES_GOOGLE_CREDS_PATH", _DEFAULT_CREDS_PATH))


def load_credentials():
    path = _creds_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"es: Google not authorized (no creds at {path}). "
            f"Operator: run `everstone auth google`."
        )
    creds = Credentials.from_authorized_user_file(str(path), GOOGLE_SCOPES)
    if not creds.valid:
        creds.refresh(Request())
    return creds


def calendar_service():
    return build("calendar", "v3", credentials=load_credentials(), cache_discovery=False)
```

- [ ] **Step 4: Update `scripts/auth_gcal.py` to request the union + write JSON**

- Import the shared scope list:
  ```python
  import sys
  sys.path.insert(0, "/opt/es")  # es package install root in the image
  from es.google_auth import GOOGLE_SCOPES, _DEFAULT_CREDS_PATH
  ```
- Where it builds the `Flow`, pass `scopes=GOOGLE_SCOPES` (replace the hardcoded
  calendar-only scope).
- Replace the pickle-write of the credential with a JSON write to the new store:
  ```python
  from pathlib import Path
  out = Path(_DEFAULT_CREDS_PATH)
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(creds.to_json())
  ```
  (Remove the old `pickle.dump(... gcalcli/oauth ...)` block.)

- [ ] **Step 5: Rename the operator command `auth gcal` → `auth google`**

In `scripts/everstone_cli.py`, change the decorator + name:
```python
@auth_app.command("google")
def auth_google() -> None:
    """OAuth into Google (Calendar now; more surfaces later). Authorize in
    browser, paste the failed-redirect URL back."""
    _exec("python3", "-u", "/scripts/auth_gcal.py")
```
(Keep the body delegating to `auth_gcal.py`. Update the help text that mentions
gcal.)

- [ ] **Step 6: Run to verify pass**

Run: `cd es && python -m pytest tests/test_google_auth.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add es/es/google_auth.py es/tests/test_google_auth.py scripts/auth_gcal.py scripts/everstone_cli.py
git commit -m "feat(es): JSON creds store + union scopes; everstone auth google"
```

---

### Task 3: Add `timezone` to the config schema + defaults

**Files:**
- Modify: `config/schema.json`
- Modify: `config/defaults.yaml`

- [ ] **Step 1: Add the property to `config/schema.json`** (top-level `properties`)

```json
    "timezone": {
      "type": "string",
      "description": "Operator home IANA timezone for es cal (e.g. America/Chicago). Per-call --tz overrides."
    }
```

- [ ] **Step 2: Add the default to `config/defaults.yaml`**

```yaml
timezone: America/Chicago
```

- [ ] **Step 3: Validate config still loads**

Run: `docker exec everstone python3 /scripts/configure.py` (or the project's config-validate path)
Expected: `[configure] Config validated successfully`

- [ ] **Step 4: Commit**

```bash
git add config/schema.json config/defaults.yaml
git commit -m "feat(config): timezone home default for es cal"
```

---

### Task 4: Point AGENTS.md at `es`; drop dead env vars

**Files:**
- Modify: `scripts/configure.py` (`_render_calendar_section`, the tasks section, `generate_hermes_env`)

- [ ] **Step 1: Update `_render_calendar_section`** — replace `gcal` usage with `es cal`

Change the heading + examples it renders from `gcal ...` to:
```
### Calendar — Google Calendar via `es cal`
... `es cal agenda <start> <end> --calendar "<Name>"`
... `es cal add --calendar "<Name>" --when "YYYY-MM-DD HH:MM" --duration 60 --where "..."`
... writes to read-only calendars are refused; pass `--tz <IANA>` when away from home.
```
(Keep the read-only/read-write bullet rendering from `config.gcalcli.calendars`.)

- [ ] **Step 2: Update the tasks section of AGENTS** — `everstone-tasks` → `es tasks`

In the `_AGENTS_PLATFORM_TEMPLATE` (the tasks block), change the example
invocations:
```
    es tasks add "Buy milk" --list inbox
    es tasks list --list inbox
    es tasks done <uid> --list inbox
```
And update the "permitted shell invocation" note from `everstone-tasks ...` to
`es tasks ...`.

- [ ] **Step 3: Drop dead env vars in `generate_hermes_env`**

Remove these keys from the `env_vars` dict (no source consumers; verified):
`EVERSTONE_AGENT_NAME`, `EVERSTONE_OWNER_NAME`, `TELEGRAM_OWNER_USER_ID`.
Also remove the now-unused tool vars `es` reads from config.yaml instead:
`GCALCLI_CLIENT_ID`, `GCALCLI_CLIENT_SECRET`, `EVERSTONE_GCAL_READ_ONLY`,
`EVERSTONE_GCAL_READ_WRITE`, `EVERSTONE_GROUP_BINARIES`,
`EVERSTONE_CALDAV_URL/USER/PASSWORD`, `EVERSTONE_VAULT_NAME`.
Keep what services/setup still need: `HERMES_MODEL`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_ALLOWED_USERS`, `TELEGRAM_COMMANDS`, `GH_TOKEN`, `EVERSTONE_SKILLS`,
`HERMES_TERMINAL_CWD`, `EVERSTONE_PUBLIC_URL`.

> NOTE: `EVERSTONE_PUBLIC_URL` stays (auth_gcal.py needs it for the OAuth
> redirect). `auth_gcal.py` reads `GCALCLI_CLIENT_ID/SECRET` for the flow — keep
> those two ONLY if the operator flow still needs them; if the client creds move
> into config.yaml read directly by auth_gcal, drop them too. Verify against the
> Task 2 auth_gcal edits before removing.

- [ ] **Step 4: Re-render + verify AGENTS.md mentions es**

Run: `docker exec everstone sh -c 'python3 /scripts/configure.py && grep -c "es cal\|es tasks" /opt/data/AGENTS.md'`
Expected: ≥ 1 (es references present); no `gcal`/`everstone-tasks` left.

- [ ] **Step 5: Commit**

```bash
git add scripts/configure.py
git commit -m "feat(configure): AGENTS.md uses es cal/es tasks; drop dead+tool env vars"
```

---

### Task 5: Trim service env injection + drop gcalcli dir setup

**Files:**
- Modify: `services/hermes/run`
- Modify: `scripts/setup_hermes`

- [ ] **Step 1: Trim `services/hermes/run`** — stop loading the whole envdir

Replace the line `s6-envdir -fn /opt/config/hermes/envdir` with a scoped
injection of only what the gateway process needs:
```
export HERMES_TERMINAL_CWD /opt/data
```
(`HERMES_HOME` is already exported just below; `es` and its tools read
`config.yaml` directly so the gateway no longer needs the tool env. Services
that still need a value get it via their own `run`/`with-contenv`.)

> If the gateway itself still needs `HERMES_MODEL`/`TELEGRAM_*` at runtime
> (it reads the profile config for those, set by setup_hermes), no env needed.
> Verify the gateway boots and answers after this trim.

- [ ] **Step 2: Drop the gcalcli dir block in `scripts/setup_hermes`**

Remove the `if [ -n "${GCALCLI_CLIENT_ID:-}" ] … mkdir -p /opt/data/hermes/gcalcli …`
block (gcalcli is gone; `es` uses the JSON creds store created by `auth_gcal.py`).

- [ ] **Step 3: Verify boot**

Run: `just dev` (or rebuild+restart), then
`docker exec everstone everstone status` and send a test DM.
Expected: gateway up; agent answers; `es tasks list` works in DM.

- [ ] **Step 4: Commit**

```bash
git add services/hermes/run scripts/setup_hermes
git commit -m "feat(s6): trim gateway env to HERMES_TERMINAL_CWD; drop gcalcli dir"
```

---

### Task 6: Image — install `es`, drop gcalcli + old binaries

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Drop gcalcli from the pip install** (lines ~101–106)

Remove the `"gcalcli>=4.4"` entry from the `pip install --break-system-packages`
list. Add the Google client libs `es` needs if not already present via `es`'s
own install (next step pulls them transitively).

- [ ] **Step 2: COPY + install the `es` package** (near the `everstone_tasks`
install, ~line 109)

```dockerfile
COPY es /opt/es
RUN pip install --break-system-packages /opt/es
```
(`es`'s `pyproject.toml` `[project.scripts] es = es.main:main` creates the `es`
console script on PATH — no manual symlink needed. It also pulls
`google-api-python-client`/`google-auth` + `everstone-tasks`.)

- [ ] **Step 3: Remove the `gcal` symlink** (line ~140)

Delete the `ln -sf /scripts/gcal /usr/local/bin/gcal && \` line from the RUN at
~138. Keep the `everstone` symlink.

- [ ] **Step 4: Remove the superseded sources**

```bash
git rm scripts/gcal
```
(Keep `everstone_tasks/` — `es tasks` imports its `TasksClient` lib. Remove its
console-script entry if its `pyproject.toml` declares `everstone-tasks`, and
delete `everstone_tasks/everstone_tasks/mcp.py`.)

- [ ] **Step 5: Build + boot**

Run: `just dev`
Expected: image builds without gcalcli; `docker exec everstone es --help` lists
`cal` + `tasks`; `docker exec everstone es tasks list` returns a JSON envelope.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile && git rm scripts/gcal
git commit -m "build: install es, drop gcalcli + gcal/everstone-tasks binaries"
```

---

### Task 7: Update the calendar skill to call `es cal`

**Files:**
- Modify: `<DATA_DIR>/hermes/profiles/everstone/skills/calendar/SKILL.md`
  (in dev: `.devm/.everstone/hermes/profiles/everstone/skills/calendar/SKILL.md`)

- [ ] **Step 1: Replace `gcal` with `es cal` throughout the skill**

- `prerequisites.commands: [es]`
- All command examples `gcal …` → `es cal …` (agenda/search/conflicts/add/edit/delete; keep `--tz`, `--calendar`, `--when`, `--duration`, `--where`).
- Note output is JSON (`--pretty` for readable); times are returned already in Central (or the `--tz` you pass) — no UTC math.

- [ ] **Step 2: Verify the agent loads it**

Run: `docker exec everstone hermes -p everstone skills list | grep calendar`
Expected: `calendar … enabled`.

- [ ] **Step 3: Commit** (the data-dir skill is gitignored; if you keep a tracked
template, update that instead)

```bash
# If the skill is templated/tracked, commit the template; otherwise it persists
# in the mounted data dir and needs no commit.
git status --short | grep -i skill || echo "skill is data-dir only (no commit)"
```

---

### Task 8: End-to-end verification

- [ ] **Step 1: Full unit suites green**

Run:
```bash
cd es && python -m pytest -q
cd ../everstone_tasks && python -m pytest -q
cd ../access_hook && python -m pytest -q
```
Expected: all green.

- [ ] **Step 2: e2e boot + behavior**

Run: `just e2e` (and/or manual): boot container; in DM ask the agent to add +
read a calendar event and a task; confirm Central times and single replies.
Expected: `es cal`/`es tasks` work end-to-end; group chat blocks non-`es-tasks`.

- [ ] **Step 3: Operator auth round-trip**

Run: `just es auth google` (or `everstone auth google`), consent, then
`docker exec everstone es cal agenda <today> <tomorrow> --calendar Family`.
Expected: JSON events; creds at `/opt/data/hermes/es/google-credentials.json`.

- [ ] **Step 4: Commit any e2e fixups**

```bash
git add -A && git commit -m "test(e2e): es cutover verified" --allow-empty
```

---

## Follow-ups (separate, optional)

- Rename the operator `everstone` admin CLI to clear the namespace.
- Make `just es <args>` transparently run the in-container `es` agent CLI.

## Self-Review

- **Spec coverage (Plan 3 slice):** ✅ access_hook → `es tasks` + fail-closed (Task 1); shared auth union scopes + operator `everstone auth google` + JSON store relocation (Task 2); `timezone` schema field (Task 3); AGENTS.md → es + drop dead/tool env vars (Task 4); trim `hermes/run` + drop gcalcli dir (Task 5); Dockerfile install es / drop gcalcli + old binaries (Task 6); calendar skill → es cal (Task 7); e2e (Task 8). Follow-ups (admin rename, `just es` passthrough) explicitly deferred.
- **Placeholder scan:** edits are concrete (anchor text + before/after). The two `> NOTE` blocks flag verify-against-built-code decisions (gcalcli client-id retention for auth_gcal; gateway runtime env), which are genuine conditionals, not placeholders.
- **Type/Name consistency:** `es.google_auth.GOOGLE_SCOPES` + `_DEFAULT_CREDS_PATH` are defined in Task 2 and imported by `auth_gcal.py` (Task 2) — same names; the JSON store path is identical in `google_auth` default and `auth_gcal` write and Task 8 verification (`/opt/data/hermes/es/google-credentials.json`); the group allowlist `("es","tasks")` in Task 1 matches the AGENTS "permitted invocation" note in Task 4.
- **Cross-plan dep:** Task 2 modifies `es/es/google_auth.py` from Plan 2 (pickle→JSON) — execute Plan 2 first.
