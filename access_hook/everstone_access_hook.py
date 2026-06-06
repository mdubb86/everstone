"""Hermes pre_tool_call plugin: per-chat tool gating for EverStone.

Install: pip install /opt/access_hook
Entry point: hermes_plugins = everstone_access_hook:HermesPlugin

Policy (current, post CLI-first refactor):

- DM (owner's private chat) — no restriction; you trust yourself with
  your own VM and the assistant persona is shaped via SOUL.md/AGENTS.md
  rather than hard ACL.
- Groups — only `es tasks ...` is callable, invoked via the
  terminal/shell tool. We check tool_name AND argv[0:2] AND reject shell
  composition (pipes, &&, ;) to keep group reach surgical.
- Empty / unparseable chat key — fail closed.

The argv check is shallow on purpose: we want a simple, auditable rule.
"""

from __future__ import annotations

import os
import shlex
from typing import Any, Optional

_BLOCK = {"action": "block", "message": "Tool not permitted outside a private DM."}

# Tool names that mean "run a shell command." Hermes's primary one is
# "terminal"; we accept aliases for safety in case future versions rename it.
_SHELL_TOOL_NAMES = {"terminal", "shell", "bash"}

# Shell composition operators we reject in group chats. The agent in a group
# should only run a single discrete `es tasks ...` invocation.
_GROUP_FORBIDDEN_SUBSTRINGS = ("|", ";", "&&", "||", "`", "$(", ">", "<", "\n")

# In groups, only `es tasks ...` is allowed (was {everstone-tasks} pre-es).
_GROUP_ALLOWED = ("es", "tasks")  # (argv[0], argv[1])


def _chat_type() -> Optional[str]:
    """Parse chat_type from HERMES_SESSION_KEY (= agent:main:<platform>:<chat_type>:<chat_id>)."""
    key = os.environ.get("HERMES_SESSION_KEY", "")
    parts = key.split(":") if key else []
    if len(parts) >= 5 and parts[0] == "agent":
        return parts[3]
    return None


def _es_tasks_invocation(command: str) -> bool:
    """Return True iff command is an `es tasks ...` invocation (no composition)."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return len(argv) >= 2 and argv[0] == _GROUP_ALLOWED[0] and argv[1] == _GROUP_ALLOWED[1]


def policy(tool_name: str, tool_input: Optional[dict] = None) -> Optional[dict]:
    """Return None to allow the call, or a block dict to deny."""
    ctype = _chat_type()

    # Owner DM — no restriction. Persona/AGENTS guide the agent's behavior.
    if ctype == "dm":
        return None

    # Anywhere else (group, supergroup, channel, unknown, missing) — fail closed
    # unless the call is a single `es tasks ...` shell invocation.
    if tool_name not in _SHELL_TOOL_NAMES:
        return _BLOCK

    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command", "")).strip()
    if not command:
        return _BLOCK

    # No shell composition in groups — sharp, single binary.
    if any(op in command for op in _GROUP_FORBIDDEN_SUBSTRINGS):
        return _BLOCK

    if not _es_tasks_invocation(command):
        return _BLOCK

    return None


class HermesPlugin:
    """Hermes plugin entry-point class."""

    def pre_tool_call(self, tool_name: str, **kwargs: Any):
        try:
            return policy(tool_name, kwargs.get("args") or kwargs.get("tool_input"))
        except Exception:
            # Hermes is fail-OPEN on hook exceptions; we fail CLOSED in groups.
            return None if _chat_type() == "dm" else _BLOCK
