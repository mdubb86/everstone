"""Hermes pre_tool_call plugin: tasks-only in group chats; all tools in owner DM.

Install: pip install /opt/access_hook
Entry point: hermes_plugins = everstone_access_hook:HermesPlugin
"""
import os
from typing import Optional

_BLOCK = {"action": "block", "message": "Tool not permitted outside a private DM."}

def _allowed_group_tools() -> set:
    return {t.strip() for t in os.environ.get("EVERSTONE_GROUP_TOOLS", "everstone_tasks").split(",") if t.strip()}

def _chat_type() -> Optional[str]:
    """Parse chat_type from HERMES_SESSION_KEY = agent:main:{platform}:{chat_type}:{chat_id}."""
    key = os.environ.get("HERMES_SESSION_KEY", "")
    parts = key.split(":") if key else []
    if len(parts) >= 5 and parts[0] == "agent":
        return parts[3]
    return None

def policy(tool_name: str) -> Optional[dict]:
    """Return None to allow, or a block dict to deny."""
    ctype = _chat_type()
    if ctype == "dm":
        return None
    if tool_name in _allowed_group_tools():
        return None
    return _BLOCK

class HermesPlugin:
    """Hermes plugin entry-point class."""
    def pre_tool_call(self, tool_name: str, **kwargs):
        return policy(tool_name)
