#!/usr/bin/env python3
"""Idempotently remove EverStone's legacy MCP server registrations.

We initially registered `everstone_tasks` (our task MCP) and `engraph` (the
notes search MCP) here. Both were dropped:

- `everstone_tasks`: replaced by the `everstone-tasks` CLI invoked via shell.
  CLIs are cheaper in tokens, composable with pipes, discoverable via
  --help, and stable. The access hook gates group chats by checking that
  argv[0] == "everstone-tasks" (still tasks-only in groups).
- `engraph`: currently a stub binary (the real Rust build was deferred).
  Leaving it registered would let the agent call it and get useless
  output. Re-register when the real binary lands.

This script now SCRUBS those two keys from Hermes's config if they exist,
so an operator upgrading an existing install doesn't carry the stale
registrations forward.
"""
import os
from pathlib import Path
import yaml

home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
cfg_path = home / "config.yaml"
cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
if not isinstance(cfg, dict):
    cfg = {}

removed = []
servers = (cfg.get("mcp") or {}).get("servers") or {}
for key in ("everstone_tasks", "engraph"):
    if key in servers:
        del servers[key]
        removed.append(key)

# Tidy up empty mcp / servers blocks.
if not servers and "mcp" in cfg and "servers" in cfg["mcp"]:
    del cfg["mcp"]["servers"]
if "mcp" in cfg and not cfg["mcp"]:
    del cfg["mcp"]

cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(yaml.dump(cfg, default_flow_style=False))

if removed:
    print(f"[merge_hermes_mcp] removed stale MCP registrations: {', '.join(removed)}")
else:
    print("[merge_hermes_mcp] no MCP servers to remove (clean install).")
