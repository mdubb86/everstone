"""es tasks — CalDAV tasks via the everstone_tasks TasksClient (in-process)."""
from datetime import datetime
from typing import Optional, Tuple

import typer

from es import config
from es.runner import envelope
from everstone_tasks.client import TasksClient
from everstone_tasks.deeplink import build_deeplink

app = typer.Typer(no_args_is_help=True)

# group_safe: this capability is the ONLY one allowed in group chats.
GROUP_SAFE = True
# config.yaml keys this capability reads:
CONFIG_KEYS = ("caldav.user", "caldav.password", "obsidian.vault_name")


def _client() -> Tuple[TasksClient, str]:
    """Build the TasksClient + return the obsidian vault name, from config.yaml."""
    cfg = config.load_config()
    caldav = cfg.get("caldav") or {}
    vault = (cfg.get("obsidian") or {}).get("vault_name", "")
    client = TasksClient(config.CALDAV_URL, caldav.get("user", ""), caldav.get("password", ""))
    return client, vault


@app.command("list")
@envelope
def list_tasks(ctx: typer.Context,
               list_name: str = typer.Option("TODO", "--list")):
    client, _ = _client()
    return client.list_tasks(list_name)


@app.command("add")
@envelope
def add_task(ctx: typer.Context,
            summary: str = typer.Argument(...),
            list_name: str = typer.Option("TODO", "--list"),
            note: Optional[str] = typer.Option(None, "--note"),
            tag: list[str] = typer.Option(None, "--tag"),
            due: Optional[str] = typer.Option(None, "--due"),
            remind_at: Optional[str] = typer.Option(None, "--remind")):
    client, vault = _client()
    url = build_deeplink(vault, note) if note else None
    uid = client.add_task(
        summary, list_name, url=url,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        due=datetime.fromisoformat(due) if due else None,
        tags=list(tag) if tag else None,
    )
    return {"uid": uid}


@app.command("edit")
@envelope
def edit_task(ctx: typer.Context,
             uid: str = typer.Argument(...),
             list_name: str = typer.Option("TODO", "--list"),
             summary: Optional[str] = typer.Option(None, "--summary"),
             tag: list[str] = typer.Option(None, "--tag"),
             due: Optional[str] = typer.Option(None, "--due"),
             remind_at: Optional[str] = typer.Option(None, "--remind")):
    client, _ = _client()
    client.edit_task(
        uid, list_name,
        summary=summary,
        due=datetime.fromisoformat(due) if due else None,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        tags=list(tag) if tag else None,
    )
    return {"uid": uid, "edited": True}


@app.command("done")
@envelope
def done_task(ctx: typer.Context,
             uid: str = typer.Argument(...),
             list_name: str = typer.Option("TODO", "--list")):
    client, _ = _client()
    client.complete_task(uid, list_name)
    return {"uid": uid, "status": "COMPLETED"}


@app.command("delete")
@envelope
def delete_task(ctx: typer.Context,
               uid: str = typer.Argument(...),
               list_name: str = typer.Option("TODO", "--list")):
    client, _ = _client()
    client.delete_task(uid, list_name)
    return {"uid": uid, "deleted": True}


@app.command("lists")
@envelope
def lists(ctx: typer.Context):
    client, _ = _client()
    return client.list_collections()
