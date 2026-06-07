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
    """Read/write values from the live Hermes profile files.

    `hermes config get` does NOT exist in this version of Hermes; the valid
    subcommands are show, edit, set, path, env-path, check, migrate.
    We read values directly from the two files Hermes manages:

      - TELEGRAM_BOT_TOKEN  → stored in `.env`  (KEY=VALUE lines, no spaces)
      - TELEGRAM_ALLOWED_USERS → stored in `config.yaml` (top-level key, integer)

    `hermes config set KEY VALUE` correctly routes each key to the right file,
    so we keep using it for writes.
    """

    def _env_path(self) -> str:
        r = subprocess.run(
            ["hermes", "-p", PROFILE, "config", "env-path"],
            capture_output=True, text=True, check=True,
        )
        return r.stdout.strip()

    def _config_path(self) -> str:
        r = subprocess.run(
            ["hermes", "-p", PROFILE, "config", "path"],
            capture_output=True, text=True, check=True,
        )
        return r.stdout.strip()

    def get(self, key: str) -> str:
        if key == "TELEGRAM_BOT_TOKEN":
            # Read from .env: lines of the form KEY=VALUE
            try:
                with open(self._env_path(), encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(key + "="):
                            return line[len(key) + 1:]
            except FileNotFoundError:
                pass
            return ""
        else:
            # Read from config.yaml: top-level key, value may be an integer
            import yaml
            try:
                with open(self._config_path(), encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                val = cfg.get(key)
                return str(val) if val is not None else ""
            except FileNotFoundError:
                return ""

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
