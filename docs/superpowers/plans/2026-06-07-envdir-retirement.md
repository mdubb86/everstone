# Envdir Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the parallel s6 envdir + sourceable env file that `configure.py` generates, making `/opt/config.yaml` the single source of truth — every consumer reads `config.yaml` directly (or gets a constant exported).

**Architecture:** Three consumers migrate off the envdir: (1) the **gateway** run-script exports the one constant it needs (`HERMES_TERMINAL_CWD`) and drops `s6-envdir`; (2) **Python** consumers (`everstone_cli`, `auth_gcal`) read `config.yaml` via `es.config.load_config`; (3) **`setup_hermes`** (shell) reads scalars via a new `scripts/config-get` helper and the `setMyCommands` POST moves into `configure.py` (Python). Then `generate_hermes_env` is deleted.

**Tech Stack:** Python 3 (Typer CLI + plain scripts + `es.config`), execline s6 run-script, pytest, urllib (Telegram Bot API).

**Verified facts (don't re-investigate):**
- `es/es/config.py::load_config()` reads `/opt/config.yaml` (override via `ES_CONFIG_PATH`); `es` is pip-installed in the image so `from es.config import load_config` works in-container.
- The gateway reads the allowlist from the `TELEGRAM_ALLOWED_USERS` env var; `gateway/run.py` (~L901) bridges **top-level `config.yaml` scalars → env** but **only if the key is not already in `os.environ`** (fallback). The allowlist is a top-level key in the Hermes profile `config.yaml` (set by `assert_telegram` at boot), so with the envdir gone the bridge WILL populate it. The token lives in the profile `.env` (loaded natively). So the gateway no longer needs the envdir for token/allowlist — only `HERMES_TERMINAL_CWD`.
- `assert_telegram.py` already reads `/opt/config.yaml` directly (not the env), so the boot-time security assert is unaffected.

**⚠️ HIGHEST RISK — Task 2 (gateway run-script).** Dropping `s6-envdir` is the EXACT change that broke the allowlist twice this session — but it is now safe because the allowlist is a top-level Hermes-config key (it wasn't then). Task 2 MUST rebuild + verify the live allowlist and report BLOCKED on any regression.

---

## File Structure

- `scripts/auth_gcal.py` — read gcal creds + public_url from `config.yaml` (not env).
- `scripts/everstone_cli.py` — remove `_load_envdir` + its import-time call; `auth_google` pre-check reads `config.yaml`.
- `services/hermes/run` — `export HERMES_TERMINAL_CWD /opt/data`, drop `s6-envdir`.
- `scripts/config-get` — **NEW.** Print a dotted key from `config.yaml` for shell consumers.
- `scripts/configure.py` — add `_telegram_commands` + `set_telegram_commands` (moved setMyCommands); delete `generate_hermes_env` + its call.
- `scripts/setup_hermes` — read `GH_TOKEN`/skills via `config-get`; drop the setMyCommands curl (moved); drop `. /opt/config/hermes/env`.
- `scripts/tests/test_configure.py` — remove the (now-obsolete) `generate_hermes_env` tests; add a `_telegram_commands` test.
- `scripts/tests/test_config_get.py` — **NEW.**
- `scripts/tests/test_auth_gcal_config.py` — **NEW** (the config-reading helper only).

---

## Task 1: Python consumers read config.yaml (drop _load_envdir)

**Files:**
- Modify: `scripts/auth_gcal.py` (`main()` env reads ~L70-73; add a config helper)
- Modify: `scripts/everstone_cli.py` (remove `_load_envdir` ~L16-33 + call ~L33; `auth_google` pre-check ~L87)
- Test: `scripts/tests/test_auth_gcal_config.py` (NEW)

- [ ] **Step 1: Write the failing test** — `scripts/tests/test_auth_gcal_config.py`:
```python
import os, importlib.util, pathlib

def _load_mod():
    p = pathlib.Path(__file__).parents[1] / "auth_gcal.py"
    spec = importlib.util.spec_from_file_location("auth_gcal", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_gcal_config_reads_from_config_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "public_url: https://es.example.com/\n"
        "gcalcli: {client_id: CID, client_secret: CSEC}\n"
    )
    monkeypatch.setenv("ES_CONFIG_PATH", str(cfg))
    # ensure es.config is importable (es package on path); skip if not available
    import pytest
    try:
        import es.config  # noqa
    except Exception:
        pytest.skip("es package not importable in this test env")
    m = _load_mod()
    cid, csec, url = m._gcal_config()
    assert cid == "CID"
    assert csec == "CSEC"
    assert url == "https://es.example.com"   # trailing slash stripped
```
NOTE: if `es` is not importable on the dev box, the test SKIPS (it's verified live in the container at Task 7). That's acceptable — the helper is small. If you can make `es` importable for the test (it's a local editable package under `es/`), prefer that so the test runs.

- [ ] **Step 2: Run it, verify FAIL** (`_gcal_config` undefined):
`cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_auth_gcal_config.py -v`

- [ ] **Step 3: Edit `scripts/auth_gcal.py`.** Add a helper (near the top, after imports):
```python
def _gcal_config():
    """Read gcal client creds + public_url from config.yaml (no envdir)."""
    from es.config import load_config
    cfg = load_config()
    gc = cfg.get("gcalcli") or {}
    return gc.get("client_id"), gc.get("client_secret"), (cfg.get("public_url") or "").rstrip("/")
```
In `main()`, REPLACE the three env reads:
```python
    client_id = os.environ.get("GCALCLI_CLIENT_ID")
    client_secret = os.environ.get("GCALCLI_CLIENT_SECRET")
    public_url = os.environ.get("EVERSTONE_PUBLIC_URL", "").rstrip("/")
```
WITH:
```python
    client_id, client_secret, public_url = _gcal_config()
```
Leave `ES_GOOGLE_CREDS_PATH` and `EVERSTONE_GCAL_OAUTH_PORT` env reads as-is (runtime override knobs, not envdir-sourced). Keep the existing "not configured" guard (it already handles missing client_id/secret).

- [ ] **Step 4: Edit `scripts/everstone_cli.py`.**
  - DELETE the `_load_envdir` function (~L16-30) and its call `_load_envdir()` (~L33), and the now-unused `from pathlib import Path` import IF nothing else uses it (check — `Path` may be unused after removal; remove only if unused).
  - In `auth_google` (~L87), the pre-check currently reads `os.environ.get("GCALCLI_CLIENT_ID")`. Replace it to read config.yaml:
```python
    from es.config import load_config
    _gc = load_config().get("gcalcli") or {}
    if not _gc.get("client_id") or not _gc.get("client_secret"):
        typer.echo(
            "Google is not configured.\n"
            "Set config.gcalcli.{client_id, client_secret} in config.yaml,\n"
            "restart the container, then re-run this command.",
            err=True,
        )
        raise typer.Exit(1)
```
  Keep the rest of `auth_google` (the `_exec("python3","-u","/scripts/auth_gcal.py")`).

- [ ] **Step 5: Run tests + import check.**
`cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_auth_gcal_config.py scripts/tests/test_everstone_cli.py -v`
Expected: pass (or the gcal-config test SKIPS if es unimportable on the dev box; the everstone_cli tests must pass). Also: `grep -n "_load_envdir\|os.environ.get(\"GCALCLI\|EVERSTONE_PUBLIC_URL" scripts/everstone_cli.py scripts/auth_gcal.py` → no remaining envdir-var reads (only the config-based reads).

- [ ] **Step 6: Commit.**
```bash
git add scripts/auth_gcal.py scripts/everstone_cli.py scripts/tests/test_auth_gcal_config.py
git commit -m "refactor: auth_gcal + esadmin read config.yaml directly (drop _load_envdir)"
```

---

## Task 2: Gateway run-script — export the constant, drop s6-envdir ⚠️

**Files:**
- Modify: `services/hermes/run`

- [ ] **Step 1: Read `services/hermes/run`.** Current form (verify):
```
#!/command/execlineb -P
with-contenv
s6-envdir -fn /opt/config/hermes/envdir
export HERMES_HOME /opt/data/hermes
hermes -p everstone gateway run --no-supervise
```

- [ ] **Step 2: Replace the `s6-envdir` line** with a direct export of the one constant the gateway needs:
```
#!/command/execlineb -P
with-contenv
export HERMES_TERMINAL_CWD /opt/data
export HERMES_HOME /opt/data/hermes
hermes -p everstone gateway run --no-supervise
```

- [ ] **Step 3: Rebuild + CRITICAL allowlist verification.**
```bash
cd /Users/michael/workspace/everstone && just dev
sleep 14
echo "--- latest boot: warning or clean? ---"
docker exec everstone sh -c 'tac /opt/data/hermes/profiles/everstone/logs/gateway.log | grep -m1 -E "No user allowlists|Gateway running with"'
echo "--- gateway env has the allowlist (via the config.yaml->env bridge)? ---"
docker exec everstone sh -c "P=\$(pgrep -f 'gateway run'|head -1); tr '\0' '\n' </proc/\$P/environ | grep -E 'TELEGRAM_ALLOWED_USERS|HERMES_TERMINAL_CWD'"
```
PASS: the latest log line is "Gateway running with 1 platform(s)" (NOT "No user allowlists"); the gateway env shows BOTH `TELEGRAM_ALLOWED_USERS=1095600876` (bridged from the Hermes config.yaml) and `HERMES_TERMINAL_CWD=/opt/data`. If the allowlist warning appears OR `TELEGRAM_ALLOWED_USERS` is missing → STOP, report **BLOCKED** with all output (the bridge isn't firing; do NOT commit).

- [ ] **Step 4: Confirm AGENTS.md still discovered + bot works** (HERMES_TERMINAL_CWD): `docker exec everstone sh -c 'grep -c "es cal\|es tasks" /opt/data/AGENTS.md'` → non-zero (AGENTS rendered), and the gateway log shows no terminal-cwd errors.

- [ ] **Step 5: Commit (only if Step 3 PASSES).**
```bash
git add services/hermes/run
git commit -m "refactor(gateway): export HERMES_TERMINAL_CWD directly; drop s6-envdir (allowlist now in Hermes config)"
```

---

## Task 3: `config-get` helper

**Files:**
- Create: `scripts/config-get`
- Test: `scripts/tests/test_config_get.py` (NEW)

- [ ] **Step 1: Write the failing test** — `scripts/tests/test_config_get.py`:
```python
import subprocess, sys, pathlib

SCRIPT = str(pathlib.Path(__file__).parents[1] / "config-get")

def _run(key, cfg_path):
    return subprocess.run([sys.executable, SCRIPT, key], capture_output=True, text=True,
                          env={"ES_CONFIG_PATH": str(cfg_path)})

def test_scalar(tmp_path):
    c = tmp_path / "config.yaml"; c.write_text("telegram: {bot_token: TKN}\n")
    r = _run("telegram.bot_token", c)
    assert r.returncode == 0 and r.stdout.strip() == "TKN"

def test_missing_is_empty(tmp_path):
    c = tmp_path / "config.yaml"; c.write_text("telegram: {bot_token: TKN}\n")
    r = _run("github.token", c)
    assert r.returncode == 0 and r.stdout.strip() == ""

def test_list_space_joined(tmp_path):
    c = tmp_path / "config.yaml"; c.write_text("agent: {skills: [a, b, c]}\n")
    r = _run("agent.skills", c)
    assert r.returncode == 0 and r.stdout.strip() == "a b c"
```

- [ ] **Step 2: Run it, verify FAIL** (script missing):
`cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_config_get.py -v`

- [ ] **Step 3: Create `scripts/config-get`:**
```python
#!/usr/bin/env python3
"""Print a dotted key from config.yaml, for shell consumers (setup_hermes).

Usage: config-get telegram.bot_token   -> prints the value
Lists print space-joined. Missing / null -> empty string + exit 0.
Reads /opt/config.yaml (override with ES_CONFIG_PATH)."""
import os
import sys

import yaml


def _get(cfg, dotted):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: config-get <dotted.key>", file=sys.stderr)
        return 2
    path = os.environ.get("ES_CONFIG_PATH", "/opt/config.yaml")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    val = _get(cfg, sys.argv[1])
    if val is None:
        print("")
    elif isinstance(val, list):
        print(" ".join(str(v) for v in val))
    else:
        print(val)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
Make it executable: `chmod +x scripts/config-get`.

- [ ] **Step 4: Run tests, verify PASS.**
`cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_config_get.py -v`

- [ ] **Step 5: Ensure the Dockerfile installs it on PATH.** `scripts/` is `COPY`d to `/scripts` and `/scripts` is on PATH (verify: `grep -n "/scripts" Dockerfile`). So `config-get` is callable as `/scripts/config-get` or `config-get`. Confirm `chmod +x` persisted (the COPY preserves the mode; if the Dockerfile does an explicit chmod for other scripts, match it). No Dockerfile change should be needed beyond the existing `COPY scripts /scripts`.

- [ ] **Step 6: Commit.**
```bash
git add scripts/config-get scripts/tests/test_config_get.py
git commit -m "feat: config-get helper — read a dotted key from config.yaml for shell"
```

---

## Task 4: Move setMyCommands into configure.py

**Files:**
- Modify: `scripts/configure.py` (add `_telegram_commands` + `set_telegram_commands`; call in `main()`)
- Test: `scripts/tests/test_configure.py`

- [ ] **Step 1: Write the failing test** — add to `scripts/tests/test_configure.py`:
```python
def test_telegram_commands_payload():
    cfg = {"telegram": {"commands": [{"cmd": "ping", "desc": "check"}]}}
    assert configure._telegram_commands(cfg) == [{"command": "ping", "description": "check"}]

def test_telegram_commands_empty_default():
    assert configure._telegram_commands({"telegram": {}}) == []
```

- [ ] **Step 2: Run it, verify FAIL** (`_telegram_commands` undefined):
`cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py::test_telegram_commands_payload -v`

- [ ] **Step 3: Implement in `scripts/configure.py`** (ensure `import json`, `import urllib.request` at top):
```python
def _telegram_commands(config: dict) -> list:
    """Build the Bot API setMyCommands payload from config.telegram.commands."""
    return [
        {"command": c["cmd"], "description": c["desc"]}
        for c in (config["telegram"].get("commands") or [])
    ]


def set_telegram_commands(config: dict) -> None:
    """POST setMyCommands to the Telegram Bot API. Best-effort: a failure here
    (network/token) logs a warning but does not abort boot. (Moved out of
    setup_hermes as part of envdir retirement.)"""
    token = config["telegram"]["bot_token"]
    commands = _telegram_commands(config)
    body = json.dumps({"commands": commands}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setMyCommands",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[configure] Telegram slash-commands set (count: {len(commands)}).")
    except Exception as e:  # noqa: BLE001 — best-effort, never block boot
        print(f"[configure] WARN: setMyCommands failed ({e}). Bot still functional.")
```
In `main()`, after config is loaded/validated (near the other `generate_*` calls), add: `set_telegram_commands(config)`.

- [ ] **Step 4: Run tests, verify PASS.**
`cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py::test_telegram_commands_payload scripts/tests/test_configure.py::test_telegram_commands_empty_default -v`

- [ ] **Step 5: Commit.**
```bash
git add scripts/configure.py scripts/tests/test_configure.py
git commit -m "feat(configure): own setMyCommands (moved from setup_hermes)"
```

---

## Task 5: setup_hermes reads config.yaml; drop env source + the moved setMyCommands

**Files:**
- Modify: `scripts/setup_hermes`

- [ ] **Step 1: Edit `scripts/setup_hermes`.**
  - REMOVE line 4 `. /opt/config/hermes/env` (no longer sourced).
  - The git-creds block uses `$GH_TOKEN`. Replace with a `config-get` read at the top of that block:
```sh
GH_TOKEN="$(config-get github.token)"
if [ -n "$GH_TOKEN" ]; then
    ...  # unchanged body
fi
```
  - DELETE the entire `setMyCommands` curl block (it moved to `configure.py`). Add a one-line comment: `# Telegram slash-commands (setMyCommands) are set by configure.py now.`
  - The skills loop uses `$EVERSTONE_SKILLS`. Replace with:
```sh
EVERSTONE_SKILLS="$(config-get agent.skills)"
if [ -n "$EVERSTONE_SKILLS" ]; then
    for skill in $EVERSTONE_SKILLS; do
        ...  # unchanged body
    done
fi
```
  - The final echo (`Run 'just model …'`) stays. `assert_telegram.py` call + `terminal.backend` + profile create stay (they don't use the env file).
  - Confirm NOTHING else in the file still references a `$VAR` that came only from the sourced env (grep: `grep -noE '\$\{?[A-Z_]{4,}' scripts/setup_hermes | sort -u` — the only ALLCAPS vars left should be `HERMES_HOME` (exported at top, line 3), `GH_TOKEN`/`EVERSTONE_SKILLS` (now from config-get), and `TELEGRAM_BOT_TOKEN` ONLY if something still needs it — but setMyCommands moved out, so `TELEGRAM_BOT_TOKEN` should no longer appear. If it does, that reference moved to configure.py or is dead — resolve it).

- [ ] **Step 2: Syntax check.** `sh -n scripts/setup_hermes` → no output.

- [ ] **Step 3: Rebuild + verify boot + allowlist + skills + setMyCommands.**
```bash
cd /Users/michael/workspace/everstone && just dev && sleep 14
docker exec everstone sh -c 'tac /opt/data/hermes/profiles/everstone/logs/gateway.log | grep -m1 -E "No user allowlists|Gateway running with"'
docker exec everstone sh -c "P=\$(pgrep -f 'gateway run'|head -1); tr '\0' '\n' </proc/\$P/environ | grep TELEGRAM_ALLOWED_USERS"
docker logs everstone 2>&1 | grep -iE "assert_telegram|slash-commands|skill ready|setMyCommands" | tail -8
```
PASS: no allowlist warning on the latest boot; gateway env has the allowlist; `[assert_telegram] … asserted`; `[configure] Telegram slash-commands set` (from configure.py now); skills lines as before. If the allowlist regresses → BLOCKED.

- [ ] **Step 4: Commit.**
```bash
git add scripts/setup_hermes
git commit -m "refactor(setup_hermes): read config.yaml via config-get; drop env source + setMyCommands (moved to configure.py)"
```

---

## Task 6: Delete generate_hermes_env + clean its tests

**Files:**
- Modify: `scripts/configure.py` (delete `generate_hermes_env` ~L353-410 + its call ~L485)
- Modify: `scripts/tests/test_configure.py` (remove the obsolete `generate_hermes_env` tests)

- [ ] **Step 1: Confirm nothing reads the envdir/env file anymore.**
`grep -rn "/opt/config/hermes/env\|generate_hermes_env\|s6-envdir\|/opt/config/hermes/envdir" scripts/ services/ Dockerfile` — the ONLY remaining hits should be the definition + call in `configure.py` (about to be deleted). If `services/hermes/run` or anything else still references the envdir → that's a missed consumer; STOP and resolve before deleting.

- [ ] **Step 2: Delete `generate_hermes_env`** (the whole function) from `scripts/configure.py`, and its call `generate_hermes_env(config)` in `main()` (~L485, plus the `print("[configure] Generating hermes envdir")` line if present just above it). Remove now-unused imports IF they become unused (`shlex` was used only by the env-file writer — check `grep -n "shlex" scripts/configure.py`; if only the deleted function used it, remove `import shlex`). Keep `json` (now used by `_telegram_commands`/`set_telegram_commands`).

- [ ] **Step 3: Remove obsolete tests.** In `scripts/tests/test_configure.py`, delete every test that calls `configure.generate_hermes_env` (e.g. `test_generate_hermes_env*`, `test_generate_hermes_env_telegram_commands_default_empty`, `test_generate_hermes_env_skills_*`, `test_generate_hermes_env_gh_token_unset`, `test_generate_hermes_env_no_longer_writes_model`, `test_generate_hermes_env_sets_terminal_cwd`, the gcalcli ones that target the envdir). Keep tests for `generate_hermes_soul`, `generate_agents_md`, `_telegram_commands`, and the schema test. (This also clears several of the 7 pre-existing failures that were tied to `generate_hermes_env`.)

- [ ] **Step 4: Run the suite.**
`cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py -v`
Expected: no errors referencing `generate_hermes_env`. Remaining failures (if any) must be ones NOT related to `generate_hermes_env` (e.g. a soul/agents_md fixture issue that predates this work) — note them, don't fix out-of-scope rot beyond removing the dead `generate_hermes_env` tests. Confirm: `grep -n "generate_hermes_env" scripts/tests/test_configure.py` → no matches.

- [ ] **Step 5: Commit.**
```bash
git add scripts/configure.py scripts/tests/test_configure.py
git commit -m "refactor(configure): delete generate_hermes_env — config.yaml is the single source"
```

---

## Task 7: End-to-end verification

- [ ] **Step 1: Unit suites.**
```bash
cd /Users/michael/workspace/everstone
python3 -m pytest scripts/tests/ -q          # config-get, _telegram_commands, auth_gcal_config, configure, everstone_cli, assert_telegram
(cd es && uv run pytest -q | tail -2)
(cd access_hook && uv run --with pytest pytest -q | tail -2)
```
Expected: es 30, access_hook 17; scripts/tests green except any soul/agents_md rot NOT tied to the deleted function (note them).

- [ ] **Step 2: Clean boot + the twice-broken allowlist.**
```bash
just dev && sleep 14
docker exec everstone sh -c 'tac /opt/data/hermes/profiles/everstone/logs/gateway.log | grep -m1 -E "No user allowlists|Gateway running with"'
docker exec everstone sh -c "P=\$(pgrep -f 'gateway run'|head -1); tr '\0' '\n' </proc/\$P/environ | grep -E 'TELEGRAM_ALLOWED_USERS|HERMES_TERMINAL_CWD'"
```
Expected: no allowlist warning; gateway env has both vars.

- [ ] **Step 3: Tools + auth path still work.**
```bash
docker exec everstone es tasks list --list inbox 2>&1 | head -c 200; echo
docker exec everstone es cal agenda 2026-06-08 2026-06-10 --calendar Family 2>&1 | head -c 200; echo
docker exec everstone esadmin calendars 2>&1 | head -3
docker exec everstone sh -c 'config-get telegram.owner_user_id; config-get agent.skills; config-get github.token'
```
Expected: `es tasks`/`es cal` return JSON (cal works against live Google creds); `esadmin calendars` lists (proves auth_gcal/google creds path reads config.yaml fine); `config-get` prints the owner id, the skills (or empty), the token (or empty).

- [ ] **Step 4: Confirm the envdir is truly gone.**
```bash
docker exec everstone sh -c 'ls /opt/config/hermes/envdir 2>&1; ls /opt/config/hermes/env 2>&1'
```
Expected: both "No such file or directory" (configure.py no longer creates them).

- [ ] **Step 5: setMyCommands + skills ran from their new homes.**
`docker logs everstone 2>&1 | grep -iE "slash-commands|skill ready|assert_telegram" | tail -6`
Expected: `[configure] Telegram slash-commands set …`; skills lines; `[assert_telegram] … asserted`.

- [ ] **Step 6: Commit any e2e fixups.**
```bash
git add -A && git commit -m "test: envdir retirement e2e verification" || echo "nothing to commit"
```

---

## Self-Review notes (already applied)

- **Coverage:** Python consumers (T1), gateway (T2), config-get (T3), setMyCommands move (T4), setup_hermes (T5), delete generate_hermes_env + tests (T6), e2e (T7). ✓
- **Order de-risks:** low-risk Python first, then the risky gateway change (verified hard), then shell + the deletion last (after all consumers migrated). ✓
- **Allowlist risk:** verified in T2 Step 3, T5 Step 3, T7 Step 2 — the single thing that broke twice. ✓
- **No orphaned envdir reader:** T6 Step 1 greps for any remaining consumer before deleting. ✓
- **Type/name consistency:** `_gcal_config` (T1), `config-get`/`_get` (T3), `_telegram_commands`/`set_telegram_commands` (T4) are referenced consistently; setup_hermes (T5) calls `config-get agent.skills`/`github.token` which `_get` resolves. ✓
- **Out-of-scope rot:** T6 removes the `generate_hermes_env` tests (clearing several of the 7 pre-existing failures); any soul/agents_md fixture rot NOT tied to the deletion is left noted, not fixed here.
