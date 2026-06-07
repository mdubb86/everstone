# Config-Model Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EverStone stops managing the Hermes LLM model in `config.yaml`; it asserts only the two security-critical Telegram values (token + allowlist) into the Hermes profile every boot — with verify + loud-fail on drift — and hands LLM model+auth to a new one-time `esadmin model <value>` command.

**Architecture:** `config.yaml` keeps the two Telegram values + EverStone's own infra; `hermes.model` is removed. A Python helper (`assert_telegram.py`) does the assert+verify+loud-fail at boot, called from `setup_hermes`. `esadmin model <value>` sets `model.default`/`provider` in the Hermes config and runs the provider auth, generalizing today's codex-only `esadmin auth hermes`. The envdir mechanism stays intact (separate follow-up); we only stop rendering `HERMES_MODEL`.

**Tech Stack:** Python 3 (Typer CLI + plain scripts), s6 shell oneshot, pytest (`uv run` for `es`, plain `pytest` for `scripts/tests`), JSON-Schema config validation.

**Verified Hermes facts (do not re-investigate):**
- The Telegram allowlist is read from the `TELEGRAM_ALLOWED_USERS` env var (`gateway/platforms/telegram.py`); fail-closed if unset.
- `hermes -p everstone config set <KEY> <val>` routes **secrets** (e.g. `TELEGRAM_BOT_TOKEN`) → the profile `.env`; **non-secrets** (e.g. `TELEGRAM_ALLOWED_USERS`, top-level) → the profile `config.yaml`. The gateway bridges top-level `config.yaml` scalars → env at boot, so a top-level `TELEGRAM_ALLOWED_USERS` reaches the allowlist.
- `hermes -p everstone config get <KEY>` prints the current value (used for the discrepancy check). Confirm its exact output shape in Task 4 Step 0.
- Codex auth: `hermes -p everstone auth add openai-codex --type oauth --manual-paste`.

**RISK — the allowlist broke twice already.** Any task touching `setup_hermes` or boot MUST, after `just dev`, confirm: (a) no NEW "No user allowlists configured" line in `/opt/data/hermes/profiles/everstone/logs/gateway.log` on the latest boot, and (b) the running gateway process env contains `TELEGRAM_ALLOWED_USERS=<owner id>`. If either fails → STOP, report BLOCKED.

**OUT OF SCOPE (separate next chunk):** retiring the envdir / env-file. `setup_hermes` still `. /opt/config/hermes/env`; `configure.py` still generates the envdir. We only stop rendering `HERMES_MODEL`.

---

## File Structure

- `config/schema.json` — drop the `hermes` object + its `required` entry.
- `config/defaults.yaml` — drop the `hermes:` line.
- `scripts/configure.py` — `generate_hermes_env`: stop rendering `HERMES_MODEL`.
- `scripts/assert_telegram.py` — **NEW.** Assert+verify the two Telegram values into the Hermes profile; loud-fail on drift. Pure-ish (subprocesses `hermes config get/set`); unit-tested with a fake runner.
- `scripts/setup_hermes` — drop model/provider seeding + the inline `hermes config set TELEGRAM_*`; call `assert_telegram.py`; keep `terminal.backend local`.
- `scripts/everstone_cli.py` — replace `auth hermes` with a top-level `model <value>` command (set model+provider, run provider auth).
- `scripts/tests/test_configure.py` — update SAMPLE (drop `hermes`); assert `HERMES_MODEL` no longer rendered.
- `scripts/tests/test_assert_telegram.py` — **NEW.** Unit tests for the helper.
- `Justfile` — replace the `hermes-auth` recipe with a `model` passthrough to `esadmin model`.
- `docs/BOOTSTRAP.md`, `docs/hermes-integration.md`, `docs/architecture.md` — model is set via `esadmin model <value>`, not `config.yaml`.

---

## Task 1: Drop `hermes.model` from config schema + defaults

**Files:**
- Modify: `config/schema.json:4` (required array) and `config/schema.json:86-92` (hermes object)
- Modify: `config/defaults.yaml:9`
- Modify: `scripts/tests/test_configure.py:15` (SAMPLE)
- Test: `scripts/tests/test_configure.py`

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_configure.py`:
```python
def test_config_schema_has_no_hermes_section():
    import json, pathlib
    schema = json.loads((pathlib.Path(__file__).parents[2] / "config/schema.json").read_text())
    assert "hermes" not in schema.get("properties", {}), "hermes.model must be removed from schema"
    assert "hermes" not in schema.get("required", []), "hermes must be removed from required"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py::test_config_schema_has_no_hermes_section -v`
Expected: FAIL (`hermes` still present).

- [ ] **Step 3: Edit the schema**

In `config/schema.json`, line 4, remove `"hermes"` from the `required` array (so it ends `…, "telegram"]`). Delete the `hermes` property block (lines 86-92):
```json
    "hermes": {
      "type": "object",
      "required": ["model"],
      "properties": {
        "model": { "type": "string", "minLength": 1 }
      }
    },
```
(Remove the trailing comma issue: ensure the property before/after stays valid JSON.)

- [ ] **Step 4: Edit defaults + test SAMPLE**

In `config/defaults.yaml`, delete line 9 `hermes: {model: null}`.

In `scripts/tests/test_configure.py:15`, remove the `"hermes": {"model":"openai/gpt-5-codex"},` fragment from `SAMPLE`.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py -v`
Expected: PASS (new test passes; existing tests still pass — they no longer rely on `hermes`).

- [ ] **Step 6: Commit**

```bash
git add config/schema.json config/defaults.yaml scripts/tests/test_configure.py
git commit -m "feat(config): drop hermes.model — model is now set via esadmin model"
```

---

## Task 2: Stop rendering `HERMES_MODEL` in configure.py

**Files:**
- Modify: `scripts/configure.py:370` (inside `generate_hermes_env`)
- Test: `scripts/tests/test_configure.py`

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_configure.py`:
```python
def test_generate_hermes_env_no_longer_writes_model(tmp_path):
    import scripts.configure as configure  # adjust import to match existing tests' style
    with _redirect_data_dir(tmp_path):  # use the same fixture/pattern the other generate_hermes_env tests use
        configure.generate_hermes_env(SAMPLE)
    envdir = tmp_path / "hermes" / "envdir"
    assert not (envdir / "HERMES_MODEL").exists(), "HERMES_MODEL must no longer be rendered"
```
NOTE: match the exact import + data-dir redirection the existing `test_generate_hermes_env_*` tests use (see `test_generate_hermes_env_sets_terminal_cwd` at line 113). Reuse that fixture rather than inventing `_redirect_data_dir`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py::test_generate_hermes_env_no_longer_writes_model -v`
Expected: FAIL (HERMES_MODEL file present).

- [ ] **Step 3: Remove the HERMES_MODEL line**

In `scripts/configure.py`, inside `generate_hermes_env`, delete line 370:
```python
        "HERMES_MODEL": config["hermes"]["model"],
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_configure.py -v`
Expected: PASS. (No other code reads `config["hermes"]` — verify with `grep -rn 'config\["hermes"\]\|hermes.\{0,3\}model' scripts/`.)

- [ ] **Step 5: Commit**

```bash
git add scripts/configure.py scripts/tests/test_configure.py
git commit -m "feat(configure): stop rendering HERMES_MODEL (model left config.yaml)"
```

---

## Task 3: `assert_telegram.py` helper (assert + verify + loud-fail)

**Files:**
- Create: `scripts/assert_telegram.py`
- Test: `scripts/tests/test_assert_telegram.py`

The helper enforces that the Hermes profile's `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USERS` match `config.yaml`. Design for testability: a `_runner` indirection so tests inject a fake `hermes config get/set`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_assert_telegram.py`:
```python
import pytest
import scripts.assert_telegram as at


class FakeHermes:
    """Stand-in for `hermes -p everstone config get/set`."""
    def __init__(self, current):
        self.current = dict(current)      # key -> value already in the Hermes config
        self.sets = []                    # records (key, value) writes

    def get(self, key):
        return self.current.get(key, "")

    def set(self, key, value):
        self.sets.append((key, value))
        self.current[key] = value


def test_asserts_values_when_absent():
    h = FakeHermes(current={})
    at.assert_telegram(token="TKN", allowed="111", hermes=h)
    assert ("TELEGRAM_BOT_TOKEN", "TKN") in h.sets
    assert ("TELEGRAM_ALLOWED_USERS", "111") in h.sets


def test_idempotent_when_matching():
    h = FakeHermes(current={"TELEGRAM_BOT_TOKEN": "TKN", "TELEGRAM_ALLOWED_USERS": "111"})
    at.assert_telegram(token="TKN", allowed="111", hermes=h)
    # already correct -> no error; re-set is allowed but must not raise
    # (we assert no exception is the real check)


def test_loud_fail_on_token_discrepancy():
    h = FakeHermes(current={"TELEGRAM_BOT_TOKEN": "OTHER", "TELEGRAM_ALLOWED_USERS": "111"})
    with pytest.raises(at.TelegramDrift) as e:
        at.assert_telegram(token="TKN", allowed="111", hermes=h)
    assert "TELEGRAM_BOT_TOKEN" in str(e.value)


def test_loud_fail_on_allowlist_discrepancy():
    h = FakeHermes(current={"TELEGRAM_BOT_TOKEN": "TKN", "TELEGRAM_ALLOWED_USERS": "111,999"})
    with pytest.raises(at.TelegramDrift) as e:
        at.assert_telegram(token="TKN", allowed="111", hermes=h)
    assert "TELEGRAM_ALLOWED_USERS" in str(e.value)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_assert_telegram.py -v`
Expected: FAIL (`No module named scripts.assert_telegram`).

- [ ] **Step 3: Write the helper**

Create `scripts/assert_telegram.py`:
```python
#!/usr/bin/env python3
"""Assert the two security-critical Telegram values into the Hermes profile.

config.yaml is authoritative for `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USERS`.
We ENFORCE them every boot, and treat any pre-existing divergent value as DRIFT —
a loud failure — rather than silently re-stomping it. (A widened allowlist or a
swapped token is a security event the operator must see.)
"""
from __future__ import annotations

import subprocess
import sys

PROFILE = "everstone"
_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS")


class TelegramDrift(RuntimeError):
    """Raised when the live Hermes value diverges from config.yaml."""


class _RealHermes:
    def get(self, key: str) -> str:
        r = subprocess.run(
            ["hermes", "-p", PROFILE, "config", "get", key],
            capture_output=True, text=True,
        )
        # `config get` prints the value (or nothing/error if unset). Treat any
        # non-zero exit or empty output as "unset".
        return r.stdout.strip() if r.returncode == 0 else ""

    def set(self, key: str, value: str) -> None:
        subprocess.run(["hermes", "-p", PROFILE, "config", "set", key, value], check=True)


def assert_telegram(token: str, allowed: str, hermes=None) -> None:
    hermes = hermes or _RealHermes()
    want = {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ALLOWED_USERS": allowed}
    drift = []
    for key in _KEYS:
        current = hermes.get(key)
        if current and current != want[key]:
            drift.append(key)
    if drift:
        raise TelegramDrift(
            "Hermes config diverged from config.yaml for: " + ", ".join(drift)
            + ". config.yaml is authoritative for these security values; "
            + "refusing to silently overwrite. Fix config.yaml or the Hermes "
            + "config so they agree, then restart."
        )
    for key in _KEYS:
        hermes.set(key, want[key])


def _load_config():
    import yaml
    with open("/opt/config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    cfg = _load_config()
    tg = cfg["telegram"]
    try:
        assert_telegram(token=tg["bot_token"], allowed=str(tg["owner_user_id"]))
    except TelegramDrift as e:
        print(f"[assert_telegram] SECURITY DRIFT: {e}", file=sys.stderr)
        return 1
    print("[assert_telegram] Telegram token + allowlist asserted from config.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_assert_telegram.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify `hermes config get` output shape (live)**

Run: `docker exec everstone hermes -p everstone config get TELEGRAM_ALLOWED_USERS`
Expected: prints `1095600876` (or the owner id) on stdout, exit 0. If the shape differs (e.g. `KEY = value`), adjust `_RealHermes.get` to parse it and re-run nothing else (helper only). Commit the adjustment with this task.

- [ ] **Step 6: Commit**

```bash
git add scripts/assert_telegram.py scripts/tests/test_assert_telegram.py
git commit -m "feat: assert_telegram helper — enforce token+allowlist, loud-fail on drift"
```

---

## Task 4: Wire `assert_telegram` into setup_hermes; drop model seeding

**Files:**
- Modify: `scripts/setup_hermes` (lines 19-35: the seed-once model block + the security asserts)

- [ ] **Step 1: Edit setup_hermes**

In `scripts/setup_hermes`, DELETE the model seed-once block (lines 19-29, the `if [ -n "${_created:-}" ]; then hermes config set model … fi`). Keep the `_created` flag logic harmless or remove `_created` if now unused.

REPLACE the security-assert lines (33-35):
```sh
hermes config set terminal.backend local
hermes config set TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
hermes config set TELEGRAM_ALLOWED_USERS "$TELEGRAM_ALLOWED_USERS"
```
WITH:
```sh
# Structural constant (agent runs tools in-container).
hermes config set terminal.backend local

# Assert the two security-critical Telegram values from config.yaml, verifying
# against drift (loud failure). config.yaml is authoritative for these. The LLM
# model + provider auth are NOT managed here — set them once with `esadmin model`.
if ! python3 /scripts/assert_telegram.py; then
    echo "[setup_hermes] FATAL: Telegram security assertion failed (see above)." >&2
    exit 1
fi
```
(`assert_telegram.py` reads `/opt/config.yaml` directly, so it does not depend on the sourced env. The `. /opt/config/hermes/env` line at top stays — other parts of setup_hermes still use it.)

- [ ] **Step 2: Syntax check**

Run: `sh -n scripts/setup_hermes`
Expected: no output (valid).

- [ ] **Step 3: Rebuild + CRITICAL allowlist verification**

Run:
```bash
cd /Users/michael/workspace/everstone && just dev
sleep 12
docker exec everstone sh -c 'grep "No user allowlists" /opt/data/hermes/profiles/everstone/logs/gateway.log | tail -2'
docker exec everstone sh -c "P=\$(pgrep -f 'gateway run'|head -1); tr '\0' '\n' </proc/\$P/environ | grep TELEGRAM_ALLOWED_USERS"
docker exec everstone sh -c 'grep -i "assert_telegram" /opt/data/hermes/profiles/everstone/logs/*.log 2>/dev/null | tail -3'
```
Expected: the latest boot has NO new "No user allowlists" line; gateway env shows `TELEGRAM_ALLOWED_USERS=<owner>`; assert_telegram logged success. If the allowlist warning reappears or the env var is missing → STOP, report BLOCKED.

- [ ] **Step 4: Commit**

```bash
git add scripts/setup_hermes
git commit -m "refactor(setup_hermes): assert Telegram via assert_telegram (loud-fail on drift); drop model seeding"
```

---

## Task 5: `esadmin model <value>` command (replaces `auth hermes`)

**Files:**
- Modify: `scripts/everstone_cli.py` (the `auth_hermes` command ~line 59-66; add a top-level `model` command)
- Test: `scripts/tests/test_everstone_cli.py` (create if absent — test the provider-derivation pure helper)

- [ ] **Step 1: Write the failing test**

Create or append `scripts/tests/test_everstone_cli.py`:
```python
import scripts.everstone_cli as cli

def test_provider_from_model():
    assert cli._provider_from_model("openai-codex/gpt-5.5") == "openai-codex"
    assert cli._provider_from_model("anthropic/claude-opus-4") == "anthropic"

def test_provider_from_model_requires_slash():
    import pytest
    with pytest.raises(SystemExit):
        cli._provider_from_model("gpt-5.5")
```
NOTE: `everstone_cli.py` calls `_load_envdir()` at import (line 32) which reads `/opt/config/hermes/envdir`. If that path is absent in the test env, guard the import — `_load_envdir` already uses `os.environ.setdefault` and tolerates a missing dir (it should no-op). Confirm the test can import the module; if `_load_envdir` raises on a missing dir, make it swallow `FileNotFoundError` (it likely already does — verify).

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_everstone_cli.py -v`
Expected: FAIL (`_provider_from_model` undefined).

- [ ] **Step 3: Implement the command**

In `scripts/everstone_cli.py`:

Add a pure helper near `_exec`:
```python
def _provider_from_model(model: str) -> str:
    """Derive the provider from a `provider/model` spec (e.g. openai-codex/gpt-5.5)."""
    if "/" not in model:
        typer.echo(f"Model must be `provider/model` (e.g. openai-codex/gpt-5.5), got '{model}'.", err=True)
        raise typer.Exit(1)
    return model.split("/", 1)[0]
```

REPLACE the `auth_hermes` command (lines 59-66) with a top-level `model` command:
```python
@app.command()
def model(
    value: str = typer.Argument(..., help="LLM as provider/model, e.g. openai-codex/gpt-5.5 or anthropic/claude-opus-4."),
) -> None:
    """Set the LLM model + run its provider auth (one-time setup for the brain)."""
    provider = _provider_from_model(value)
    # 1) Set model + provider in the Hermes profile config.
    subprocess.run(["hermes", "-p", "everstone", "config", "set", "model", value], check=True)
    subprocess.run(["hermes", "-p", "everstone", "config", "set", "provider", provider], check=True)
    # 2) Run the provider's auth. openai-codex uses the OAuth manual-paste flow;
    #    other providers fall through to Hermes' interactive `auth add`.
    if provider == "openai-codex":
        _exec("hermes", "-p", "everstone", "auth", "add", "openai-codex", "--type", "oauth", "--manual-paste")
    else:
        _exec("hermes", "-p", "everstone", "auth", "add", provider)
```
Add `import subprocess` at the top if not already imported. (`_exec` execvp-replaces the process, so it must be the LAST call — the two `subprocess.run` config-sets run first, then `_exec` hands off to the interactive auth.)

Remove the now-dead `auth_hermes` function and its `@auth_app.command("hermes")` decorator. Leave `auth_app` with just `google`.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Users/michael/workspace/everstone && python3 -m pytest scripts/tests/test_everstone_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Rebuild + smoke the surface**

Run:
```bash
cd /Users/michael/workspace/everstone && just dev
docker exec everstone esadmin --help 2>&1 | grep -E "model|auth"
docker exec everstone esadmin model --help 2>&1 | head -5
docker exec everstone esadmin auth --help 2>&1 | sed -n '/Commands/,/╰/p'
```
Expected: `model` command present; `esadmin model --help` shows the value arg; `esadmin auth` now lists only `google` (no `hermes`). Do NOT run `esadmin model …` for real here (it would re-trigger OAuth on the live profile).

- [ ] **Step 6: Commit**

```bash
git add scripts/everstone_cli.py scripts/tests/test_everstone_cli.py
git commit -m "feat(esadmin): model <value> — set model+provider and run provider auth (replaces auth hermes)"
```

---

## Task 6: Justfile + docs

**Files:**
- Modify: `Justfile` (the `hermes-auth` recipe ~line 105-108)
- Modify: `docs/BOOTSTRAP.md` (model-in-config example ~line 70-74; "Grant Hermes its model auth" ~line 114)
- Modify: `docs/hermes-integration.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Justfile**

Replace the `hermes-auth` recipe with a `model` passthrough:
```
# Set the LLM model + run its provider auth (one-time). e.g. `just model openai-codex/gpt-5.5`.
model +ARGS:
    #!/usr/bin/env bash
    if [ -t 0 ] && [ -t 1 ]; then DT="-it"; else DT="-i"; fi
    docker exec $DT {{DEV_NAME}} esadmin model {{ARGS}}
```

- [ ] **Step 2: docs/BOOTSTRAP.md**

- Remove the `model:` field from the `config.yaml` example (~line 70-74) and add a note: "The LLM model is NOT in config.yaml — set it once after boot with `just model <provider/model>`."
- Replace the "Grant Hermes its model auth" section's command (whatever invokes `esadmin auth hermes` / `just hermes-auth`) with `just model openai-codex/gpt-5.5` (or the operator's chosen model), explaining it sets the model AND runs the provider auth in one step.

- [ ] **Step 3: docs/hermes-integration.md**

- In "What EverStone seeds once", change the model row: model is no longer seeded from config.yaml — it's set via `just model <value>` (one-time), like auth. 
- In "What EverStone asserts (every boot)", note the assert is now verify+loud-fail (config.yaml authoritative; drift is an error).
- Update the `config.yaml` mental-model section: it no longer holds `hermes.model`.

- [ ] **Step 4: docs/architecture.md**

- Update the "Config & env model" section: `config.yaml` holds the two Telegram values + infra (no model); boot asserts token+allowlist with verify+loud-fail via `assert_telegram.py`; model+provider auth is the one-time `esadmin model <value>`.
- Update the `esadmin` surface line: `auth` now has only `google`; add `model` to the command list; note `auth hermes` was replaced by `model`.

- [ ] **Step 5: Commit**

```bash
git add Justfile docs/BOOTSTRAP.md docs/hermes-integration.md docs/architecture.md
git commit -m "docs+just: model set via `just model <value>`; config-model redesign"
```

---

## Task 7: End-to-end verification

- [ ] **Step 1: All unit suites green**

Run:
```bash
cd /Users/michael/workspace/everstone
python3 -m pytest scripts/tests/ -q
(cd es && uv run pytest -q | tail -2)
(cd access_hook && uv run --with pytest pytest -q | tail -2)
```
Expected: all green.

- [ ] **Step 2: Live boot + allowlist (the risk)**

Run:
```bash
just dev && sleep 12
docker exec everstone sh -c 'grep "No user allowlists" /opt/data/hermes/profiles/everstone/logs/gateway.log | tail -1'
docker exec everstone sh -c "P=\$(pgrep -f 'gateway run'|head -1); tr '\0' '\n' </proc/\$P/environ | grep TELEGRAM_ALLOWED_USERS"
```
Expected: NO new allowlist warning on the latest boot; gateway env has `TELEGRAM_ALLOWED_USERS`.

- [ ] **Step 3: Drift detection works**

Run (simulate drift, confirm loud-fail, then restore):
```bash
docker exec everstone hermes -p everstone config set TELEGRAM_ALLOWED_USERS "111,999"
docker exec everstone python3 /scripts/assert_telegram.py; echo "exit=$?"
```
Expected: prints `SECURITY DRIFT … TELEGRAM_ALLOWED_USERS`, `exit=1`. Then restore: `docker exec everstone hermes -p everstone config set TELEGRAM_ALLOWED_USERS <owner-id>` and re-run → exit 0.

- [ ] **Step 4: esadmin surface**

Run: `docker exec everstone esadmin auth --help` (only `google`) and `docker exec everstone esadmin model --help` (present).

- [ ] **Step 5: Commit any e2e fixups**

```bash
git add -A && git commit -m "test: config-model redesign e2e verification" || echo "nothing to commit"
```

---

## Self-Review notes (already applied)

- **Spec coverage:** schema/defaults drop (T1), configure HERMES_MODEL drop (T2), assert+verify+loud-fail (T3+T4), `esadmin model` with provider auth (T5), Justfile+docs (T6), e2e incl. allowlist + drift (T7). ✓
- **Envdir kept:** no task removes the envdir or the `. /opt/config/hermes/env` source; only `HERMES_MODEL` rendering is dropped. ✓
- **Allowlist risk:** verified in T4 Step 3 and T7 Step 2. ✓
- **Open confirmation (T3 Step 5 / T5):** exact `hermes config get` output shape and `hermes auth add <provider>` (non-codex) interactive behavior are verified live during implementation, not assumed.
