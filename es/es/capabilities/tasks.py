"""es tasks — CalDAV tasks via the embedded TasksClient (in-process)."""
from datetime import datetime
from typing import Optional, Tuple

import typer

from es import config
from es.runner import envelope
from es.tasks_client import TasksClient
from es.deeplink import build_deeplink

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
               list_name: str = typer.Option("TODO", "--list"),
               tag: Optional[str] = typer.Option(None, "--tag"),
               all_: bool = typer.Option(False, "--all")):
    client, _ = _client()
    items = client.list_tasks(list_name)
    if not all_:
        items = [t for t in items if str(t.get("status", "")) != "COMPLETED"]
    if tag:
        items = [t for t in items if tag in (t.get("tags") or [])]
    return items


@app.command("add")
@envelope
def add_task(ctx: typer.Context,
            summary: str = typer.Argument(...),
            list_name: str = typer.Option("TODO", "--list"),
            note: Optional[str] = typer.Option(None, "--note"),
            tag: list[str] = typer.Option(None, "--tag"),
            due: Optional[str] = typer.Option(None, "--due"),
            remind_at: Optional[str] = typer.Option(None, "--remind"),
            parent: Optional[str] = typer.Option(None, "--parent")):
    client, vault = _client()
    url = build_deeplink(vault, note) if note else None
    uid = client.add_task(
        summary, list_name, url=url,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        due=datetime.fromisoformat(due) if due else None,
        tags=list(tag) if tag else None,
        parent_uid=parent,
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
             remind_at: Optional[str] = typer.Option(None, "--remind"),
             parent: Optional[str] = typer.Option(None, "--parent")):
    client, _ = _client()
    client.edit_task(
        uid, list_name,
        summary=summary,
        due=datetime.fromisoformat(due) if due else None,
        remind_at=datetime.fromisoformat(remind_at) if remind_at else None,
        tags=list(tag) if tag else None,
        parent_uid=parent,
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
               list_name: str = typer.Option("TODO", "--list"),
               force: bool = typer.Option(False, "--force")):
    client, _ = _client()
    client.delete_task(uid, list_name, force=force)
    return {"uid": uid, "deleted": True}


@app.command("lists")
@envelope
def lists(ctx: typer.Context):
    client, _ = _client()
    return client.list_collections()


@app.command("list-create")
@envelope
def list_create(ctx: typer.Context, name: str = typer.Argument(...)):
    client, _ = _client()
    client.ensure_list(name)
    return {"list": name, "created": True}


@app.command("list-delete")
@envelope
def list_delete(ctx: typer.Context, name: str = typer.Argument(...)):
    client, _ = _client()
    client.delete_list(name)
    return {"list": name, "deleted": True}


@app.command("clear")
@envelope
def clear(ctx: typer.Context,
          name: str = typer.Argument(...),
          all_: bool = typer.Option(False, "--all")):
    client, _ = _client()
    removed = client.clear_list(name, completed_only=not all_)
    return {"list": name, "removed": removed}
