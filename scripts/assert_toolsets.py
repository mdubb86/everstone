#!/usr/bin/env python3
"""Assert the agent's tool surface into the Hermes profile, every boot.

EverStone's core security property is that the agent is locked to a curated
tool set: no shell, no filesystem, no code execution. It reaches capabilities
only through the `everstone-es` MCP server. Container isolation is the real
floor, but this is the boundary that keeps a prompt-injected agent from running
`terminal` against the vault.

Two things were wrong before this script existed:

1. THE ALLOWLIST WAS SEEDED ONCE, at profile creation, inside setup_hermes'
   `if profile does not exist` block. It was a default, not an assertion: a
   Hermes upgrade, a hand-edit, or `/config` in chat could widen it and nothing
   ever put it back. Its own comment admitted "EXISTING profiles must have
   their platform_toolsets.telegram updated by hand".

2. IT ONLY COVERED `telegram`. Cron was never listed, so cron-spawned agents
   fell through to the unconfigured-platform default — the FULL toolset.
   Measured by asking a real cron job to enumerate its own tools:

       terminal, execute_code, process, read_file, write_file, patch,
       search_files, delegate_task, browser_*, ...

   So the unattended 3am surface was strictly MORE privileged than the DM
   surface, which is exactly backwards.

Unlike assert_telegram.py this ENFORCES rather than failing on drift. A widened
toolset should self-heal on restart: refusing to boot would leave the operator
with a dead assistant and a config they cannot edit from chat, and the drift is
far more likely to be an upstream default than an attack.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

PROFILE_DIR = pathlib.Path("/opt/data/hermes/profiles/everstone")

# Telegram (the operator's DM): the interactive surface. No terminal/file/
# code_execution — the agent acts through es_* only.
TELEGRAM_TOOLSETS = ["skills", "vision", "clarify", "web", "memory", "browser"]

# Cron: deliberately NARROWER than the DM surface. An unattended agent has no
# one to catch a bad turn, so it gets es_* (merged automatically — MCP servers
# are not gated by this list unless it names them) plus skills, and nothing
# else.
#
# Notably absent:
#   browser  — general interactive browsing is the classic prompt-injection
#              vector, and es_web_fetch already covers cheap page reads.
#              es_maps_* does NOT need it: those drive camofox-AUTH server-side
#              inside es, never through the agent's browser_* tools.
#   clarify  — interactive; nothing can answer at 3am (Hermes also strips it)
#   memory   — cron agents are built skip_memory=True (Hermes also strips it)
CRON_TOOLSETS = ["skills"]

# Belt-and-braces. `enabled_toolsets -= disabled_set` runs LAST in Hermes'
# resolver, and cron merges this list into its always-disabled set specifically
# so a per-job `enabled_toolsets` cannot widen past it.
#
# HONEST LIMIT: this subtracts TOOLSET names, not tool names, and `terminal` /
# `write_file` / `execute_code` appear in ~25 toolsets (every `hermes-*`
# platform bundle among them). Naming all of them would be a list that rots
# with every Hermes release. So this is a second line of defence against the
# names most plausibly enabled by accident — the ALLOWLIST above is the real
# mechanism.
DISABLED_TOOLSETS = [
    "terminal", "coding", "file", "code_execution", "debugging", "delegation",
]


def desired() -> dict:
    return {
        "platform_toolsets": {"telegram": TELEGRAM_TOOLSETS, "cron": CRON_TOOLSETS},
        "agent": {"disabled_toolsets": DISABLED_TOOLSETS},
    }


def apply(cfg: dict) -> tuple[dict, list[str]]:
    """Merge the asserted surface into `cfg`. Returns (new_cfg, changes).

    Only the keys we own are touched — everything else in the profile config is
    operator-owned and left alone.
    """
    cfg = dict(cfg or {})
    changes: list[str] = []

    platforms = dict(cfg.get("platform_toolsets") or {})
    for name, want in (("telegram", TELEGRAM_TOOLSETS), ("cron", CRON_TOOLSETS)):
        if list(platforms.get(name) or []) != want:
            changes.append(
                f"platform_toolsets.{name}: {platforms.get(name)!r} -> {want!r}")
            platforms[name] = list(want)
    cfg["platform_toolsets"] = platforms

    agent = dict(cfg.get("agent") or {})
    if list(agent.get("disabled_toolsets") or []) != DISABLED_TOOLSETS:
        changes.append(
            f"agent.disabled_toolsets: {agent.get('disabled_toolsets')!r} "
            f"-> {DISABLED_TOOLSETS!r}")
        agent["disabled_toolsets"] = list(DISABLED_TOOLSETS)
    cfg["agent"] = agent

    return cfg, changes


def main(profile_dir: pathlib.Path = PROFILE_DIR) -> int:
    path = profile_dir / "config.yaml"
    if not path.is_file():
        # Nothing to assert yet — setup_hermes seeds the profile before calling
        # us, so this means profile creation failed and that error is louder.
        print("[assert_toolsets] no profile config yet; skipping", file=sys.stderr)
        return 0
    cfg = yaml.safe_load(path.read_text()) or {}
    new_cfg, changes = apply(cfg)
    if changes:
        path.write_text(yaml.safe_dump(new_cfg, sort_keys=False))
        for line in changes:
            print(f"[assert_toolsets] CORRECTED {line}")
    else:
        print("[assert_toolsets] tool surface matches policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
