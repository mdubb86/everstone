"""Hermes pre_tool_call plugin: per-chat tool gating for EverStone.

Install: pip install /opt/access_hook
Entry point: hermes_plugins = everstone_access_hook:HermesPlugin

Policy (MCP-only era):
- DM (owner's private chat) — no restriction; persona/AGENTS shape behavior.
- Groups / supergroups / channels / unknown — fail closed EXCEPT the group-safe
  es tools: the task tools (`es_tasks_*`), calendar tools (`es_cal_*`) and
  weather (`es_weather*`).
  Notes (`es_notes_*`) and every non-es tool are blocked.
- Empty / unparseable chat key — fail closed.

The agent has no shell, so there is no command to parse — the rule is a simple,
auditable tool-name prefix allowlist.
"""
from __future__ import annotations

import os
from typing import Any, Optional

_BLOCK = {"action": "block", "message": "Tool not permitted outside a private DM."}

# Group-safe MCP tools: tasks, calendar, weather. Notes (es_notes_*) are
# intentionally excluded — they don't belong in shared chats.
# NB: "es_weather" has no trailing underscore — the tool is a single es_weather,
# not an es_weather_* family.
_GROUP_ALLOWED_PREFIXES = ("es_tasks_", "es_cal_", "es_weather")


def _chat_type() -> Optional[str]:
    """Parse chat_type from HERMES_SESSION_KEY (agent:main:<platform>:<chat_type>:<chat_id>)."""
    key = os.environ.get("HERMES_SESSION_KEY", "")
    parts = key.split(":") if key else []
    if len(parts) >= 5 and parts[0] == "agent":
        return parts[3]
    return None


def policy(tool_name: str, tool_input: Optional[dict] = None) -> Optional[dict]:
    """Return None to allow the call, or a block dict to deny."""
    ctype = _chat_type()
    if ctype == "dm":
        return None
    # Missing / unparseable session key — fail closed (block everything).
    if ctype is None:
        return _BLOCK
    # Known non-DM chat (group / supergroup / channel): allow only the
    # group-safe es tools (tasks + calendar); block notes + everything else.
    if any(tool_name.startswith(p) for p in _GROUP_ALLOWED_PREFIXES):
        return None
    return _BLOCK


class HermesPlugin:
    """Hermes plugin entry-point class."""

    def pre_tool_call(self, tool_name: str, **kwargs: Any):
        try:
            return policy(tool_name, kwargs.get("args") or kwargs.get("tool_input"))
        except Exception:
            # Hermes is fail-OPEN on hook exceptions; we fail CLOSED outside DM.
            return None if _chat_type() == "dm" else _BLOCK
