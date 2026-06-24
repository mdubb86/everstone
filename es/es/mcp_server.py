"""es MCP server — exposes es operations as MCP tools (FastMCP).

Wraps the same in-process clients the CLI uses; returns the same
{ok,data}/{ok,error} envelope. The CLI (es.main) stays for dev/tests; the
AGENT only ever sees these tools.
"""
import functools
from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

from es import config
from es.deeplink import build_deeplink
from es.tasks_client import TasksClient

mcp = FastMCP("everstone-es")


def mcp_envelope(fn):
    """Turn a tool's return into {ok:true,data}; any exception into
    {ok:false,error:{code,message}} (mirrors the CLI @envelope). Honors an
    exception's `es_code` attribute for the error code."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except Exception as e:  # noqa: BLE001 — tool boundary: never raise to the agent
            code = getattr(e, "es_code", None) or type(e).__name__
            return {"ok": False, "error": {"code": code, "message": str(e)}}
    return wrapper


def _client():
    cfg = config.load_config()
    caldav = cfg.get("caldav") or {}
    vault = (cfg.get("obsidian") or {}).get("vault_name", "")
    return TasksClient(config.CALDAV_URL, caldav.get("user", ""), caldav.get("password", "")), vault


@mcp.tool()
@mcp_envelope
def es_tasks_list(list: str = "TODO", tag: Optional[str] = None, all: bool = False) -> list:
    """List tasks in a list (default TODO). all=true includes completed; tag filters."""
    client, _ = _client()
    items = client.list_tasks(list)
    if not all:
        items = [t for t in items if str(t.get("status", "")) != "COMPLETED"]
    if tag:
        items = [t for t in items if tag in (t.get("tags") or [])]
    return items


@mcp.tool()
@mcp_envelope
def es_tasks_add(summary: str, list: str = "TODO", note: Optional[str] = None,
                 tag: Optional[str] = None, due: Optional[str] = None,
                 remind: Optional[str] = None, parent: Optional[str] = None) -> dict:
    """Add a task to a list (default TODO). note attaches an Obsidian deeplink;
    due/remind are ISO datetimes; tag adds a single tag; parent nests as a subtask."""
    client, vault = _client()
    url = build_deeplink(vault, note) if note else None
    uid = client.add_task(
        summary, list, url=url,
        remind_at=datetime.fromisoformat(remind) if remind else None,
        due=datetime.fromisoformat(due) if due else None,
        tags=[tag] if tag else None,
        parent_uid=parent,
    )
    return {"uid": uid}


@mcp.tool()
@mcp_envelope
def es_tasks_edit(uid: str, list: str = "TODO", summary: Optional[str] = None,
                  tag: Optional[str] = None, due: Optional[str] = None,
                  remind: Optional[str] = None, parent: Optional[str] = None) -> dict:
    """Edit a task. Only provided fields change; due/remind are ISO datetimes;
    tag sets a single tag; parent re-nests the task."""
    client, _ = _client()
    client.edit_task(
        uid, list,
        summary=summary,
        due=datetime.fromisoformat(due) if due else None,
        remind_at=datetime.fromisoformat(remind) if remind else None,
        tags=[tag] if tag else None,
        parent_uid=parent,
    )
    return {"uid": uid, "edited": True}


@mcp.tool()
@mcp_envelope
def es_tasks_done(uid: str, list: str = "TODO") -> dict:
    """Mark a task complete."""
    client, _ = _client()
    client.complete_task(uid, list)
    return {"uid": uid, "status": "COMPLETED"}


@mcp.tool()
@mcp_envelope
def es_tasks_delete(uid: str, list: str = "TODO", force: bool = False) -> dict:
    """Delete a task. force=true deletes even when it has subtasks."""
    client, _ = _client()
    client.delete_task(uid, list, force=force)
    return {"uid": uid, "deleted": True}


def main() -> None:
    mcp.run()
