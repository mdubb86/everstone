#!/usr/bin/env python3
"""Register EverStone's `es` MCP server in Hermes, and scrub legacy entries.

EverStone exposes `es` (tasks + calendar) to the agent as MCP tools via a
single FastMCP server, `everstone-es` (the `es-mcp` entry point). This script
registers that server in the **active profile's** config on every boot
(idempotent upsert) and scrubs two legacy registrations that were dropped:

- `everstone_tasks`: an earlier task MCP, later replaced by a shell CLI, now
  superseded entirely by the `es_tasks_*` MCP tools on `everstone-es`.
- `engraph`: a notes-search MCP that was only ever a stub binary.

Key facts (verified against Hermes 0.16.0):
- Hermes reads MCP servers from the **top-level `mcp_servers`** key, NOT the
  older nested `mcp.servers`. (`tools_config._get_platform_tools` does
  `config.get("mcp_servers")`.) A server with no `enabled` flag defaults to
  enabled, and MCP servers reach every platform unless a platform's toolset
  list explicitly names MCP servers as an allowlist — so the locked-down
  Telegram toolset still sees `everstone-es`.
- MCP config in the **global** `$HERMES_HOME/config.yaml` does NOT merge into a
  profile, so the registration must be written to the profile config
  (`$HERMES_HOME/profiles/<active>/config.yaml`).
"""
import os
from pathlib import Path

import yaml

# The es-mcp entry point, exposed on PATH by the Dockerfile (keep this path in
# sync with the Dockerfile symlink).
ES_MCP_BIN = "/usr/local/lib/hermes-agent/.venv/bin/es-mcp"

LEGACY_KEYS = ("everstone_tasks", "engraph")


def apply(cfg: dict) -> dict:
    """Pure transform: scrub legacy MCP entries + register `everstone-es`.

    Idempotent — running it twice yields the same config. Read/write of the
    actual file stays in ``main`` so this stays unit-testable.
    """
    if not isinstance(cfg, dict):
        cfg = {}

    # Scrub legacy registrations from BOTH the old nested `mcp.servers` and the
    # current top-level `mcp_servers`, wherever an upgrading install carries
    # them.
    nested = (cfg.get("mcp") or {}).get("servers") or {}
    top = cfg.get("mcp_servers") or {}
    for key in LEGACY_KEYS:
        nested.pop(key, None)
        top.pop(key, None)

    # Tidy empty legacy blocks.
    if not nested and isinstance(cfg.get("mcp"), dict):
        cfg["mcp"].pop("servers", None)
        if not cfg["mcp"]:
            del cfg["mcp"]

    # Register the es MCP server (top-level key — what Hermes reads).
    cfg.setdefault("mcp_servers", {})["everstone-es"] = {
        "command": ES_MCP_BIN,
        "args": [],
        "env": {},
    }
    return cfg


def _profile_config_path() -> Path:
    """Path to the active profile's config.yaml under HERMES_HOME.

    MCP config does not merge from the global config into a profile, so the
    registration must land in the profile the gateway actually runs.
    """
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    active = home / "active_profile"
    name = active.read_text().strip() if active.exists() else "everstone"
    return home / "profiles" / name / "config.yaml"


def main() -> None:
    cfg_path = _profile_config_path()
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    apply(cfg)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"[merge_hermes_mcp] registered 'everstone-es' MCP server in {cfg_path}")


if __name__ == "__main__":
    main()
