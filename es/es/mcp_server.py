"""es MCP server — exposes es operations as MCP tools (FastMCP).

Wraps the same in-process clients the CLI uses; returns the same
{ok,data}/{ok,error} envelope. The CLI (es.main) stays for dev/tests; the
AGENT only ever sees these tools.
"""
import functools
from typing import Optional

from mcp.server.fastmcp import FastMCP

from es import config
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


def main() -> None:
    mcp.run()
