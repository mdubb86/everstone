import os
from datetime import datetime
from typing import Optional
from mcp.server.fastmcp import FastMCP
from .client import TasksClient
from .deeplink import build_deeplink

mcp = FastMCP("everstone_tasks")


def _client():
    return TasksClient(os.environ["EVERSTONE_CALDAV_URL"],
                       os.environ.get("EVERSTONE_CALDAV_USER", ""),
                       os.environ.get("EVERSTONE_CALDAV_PASSWORD", ""))


@mcp.tool()
def list_tasks(list_name: str = "inbox") -> list:
    """List tasks in a list."""
    return _client().list_tasks(list_name)


@mcp.tool()
def add_task(summary: str, list_name: str = "inbox",
             note_path: Optional[str] = None, remind_at: Optional[str] = None) -> dict:
    """Add a task. note_path stamps an obsidian deeplink; remind_at (ISO 8601) persists a VALARM."""
    url = build_deeplink(os.environ["EVERSTONE_VAULT_NAME"], note_path) if note_path else None
    remind = datetime.fromisoformat(remind_at) if remind_at else None
    return {"uid": _client().add_task(summary, list_name, url=url, remind_at=remind)}


@mcp.tool()
def complete_task(uid: str, list_name: str = "inbox") -> dict:
    """Mark a task complete."""
    _client().complete_task(uid, list_name); return {"uid": uid, "status": "COMPLETED"}


@mcp.tool()
def link_task(uid: str, note_path: str, list_name: str = "inbox") -> dict:
    """Set the obsidian deeplink on a task."""
    url = build_deeplink(os.environ["EVERSTONE_VAULT_NAME"], note_path)
    _client().set_note_link(uid, list_name, url); return {"uid": uid, "url": url}


def run():
    mcp.run()
