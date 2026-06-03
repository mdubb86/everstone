#!/usr/bin/env python3
"""Idempotently register MCP servers in Hermes config."""
import os, sys
from pathlib import Path
import yaml

home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
cfg_path = home / "config.yaml"
cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
if not isinstance(cfg, dict):
    cfg = {}

cfg.setdefault("mcp", {}).setdefault("servers", {})
cfg["mcp"]["servers"]["everstone_tasks"] = {
    "command": "everstone-tasks-mcp",
    "env": {},
}
cfg["mcp"]["servers"]["engraph"] = {
    "command": "engraph",
    "args": ["serve"],
    "env": {},
}

cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(yaml.dump(cfg, default_flow_style=False))
print("[merge_hermes_mcp] MCP servers registered.")
