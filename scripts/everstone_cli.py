#!/usr/bin/env python3
"""EverStone admin CLI — the operator-facing surface of the running container.

Invoked as `everstone <command>` from inside the container, or from the host
as `docker exec [-it] everstone everstone <command>`. The host-side Justfile
wraps these for dev convenience, but the in-container CLI is the source of
truth for what an operator can do at runtime.
"""
import os
from pathlib import Path

import typer


def _load_envdir(path: str = "/opt/config/hermes/envdir") -> None:
    """Populate os.environ from s6's envdir (one file per var, raw value).

    Same vars are exported by s6 service `run` scripts via s6-envdir but
    ad-hoc `docker exec everstone everstone <cmd>` calls don't go through
    s6. Read directly from the envdir form — its values are raw bytes
    with no shell quoting, so JSON values (gcal calendar lists) round-trip
    cleanly. setdefault → `docker exec -e NAME=v` overrides still win.
    """
    p = Path(path)
    if not p.is_dir():
        return
    for entry in p.iterdir():
        if entry.is_file():
            os.environ.setdefault(entry.name, entry.read_text())


_load_envdir()

app = typer.Typer(
    help="EverStone admin CLI. From the host: `docker exec [-it] everstone everstone <command>`.",
    no_args_is_help=True,
    add_completion=True,
)

auth_app = typer.Typer(help="One-time authentication flows.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")

session_app = typer.Typer(help="Inspect Hermes sessions.", no_args_is_help=True)
app.add_typer(session_app, name="session")

setup_app = typer.Typer(help="First-time setup helpers.", no_args_is_help=True)
app.add_typer(setup_app, name="setup")

calendar_app = typer.Typer(help="Google Calendar utilities (discovery, listing).", no_args_is_help=True)
app.add_typer(calendar_app, name="calendar")


def _exec(*args: str) -> None:
    os.execvp(args[0], list(args))


# ─── auth ──────────────────────────────────────────────────────────────────

@auth_app.command("hermes")
def auth_hermes() -> None:
    """OAuth into ChatGPT Codex. Authorize in your browser, paste the failed-redirect URL back."""
    _exec(
        "hermes", "-p", "everstone",
        "auth", "add", "openai-codex",
        "--type", "oauth", "--manual-paste",
    )


@auth_app.command("google")
def auth_google() -> None:
    """OAuth into Google (Calendar now; more surfaces later). Authorize in browser. One-time per Google account."""
    if not os.environ.get("GCALCLI_CLIENT_ID") or not os.environ.get("GCALCLI_CLIENT_SECRET"):
        typer.echo(
            "Google is not configured.\n"
            "Set config.gcalcli.{client_id, client_secret} in config.yaml,\n"
            "restart the container, then re-run this command.",
            err=True,
        )
        raise typer.Exit(1)
    # Delegated to /scripts/auth_gcal.py — runs our own OAuth flow on a
    # fixed port (gcalcli's built-in flow uses random ports and assumes
    # the browser can reach the container directly, which doesn't fit
    # docker-in-VM setups). Result is written as JSON to the es shared
    # credential store at /opt/data/hermes/es/google-credentials.json.
    _exec("python3", "-u", "/scripts/auth_gcal.py")


# ─── chat ──────────────────────────────────────────────────────────────────

@app.command()
def chat() -> None:
    """Interactive REPL with the agent — full reasoning + tool calls visible."""
    _exec("hermes", "-p", "everstone", "chat")


# ─── session ───────────────────────────────────────────────────────────────

@session_app.command("list")
def session_list() -> None:
    """List recent agent sessions (both CLI and Telegram)."""
    _exec("hermes", "-p", "everstone", "sessions", "list")


@session_app.command("show")
def session_show(
    session_id: str = typer.Argument(..., help="Session id from `everstone session list`."),
) -> None:
    """Replay a session — full trace including tool calls."""
    _exec("hermes", "-p", "everstone", "sessions", "show", session_id)


# ─── calendar ──────────────────────────────────────────────────────────────

@calendar_app.command("list")
def calendar_list() -> None:
    """List calendars the authed Google account can see — use this to discover IDs for config.yaml."""
    _exec("gcal", "list")


# ─── setup ─────────────────────────────────────────────────────────────────

@setup_app.command("livesync")
def setup_livesync() -> None:
    """Walk through Obsidian LiveSync onboarding (generates a one-shot URI)."""
    _exec("/scripts/setup-obsidian-livesync")


# ─── ops ───────────────────────────────────────────────────────────────────

@app.command()
def status() -> None:
    """Show s6 service status — which longruns are up, down, restarting."""
    _exec("s6-rc", "-a", "list")


@app.command("sync-state")
def sync_state() -> None:
    """One-screen LiveSync diagnostic — bridge state, doc counts, last edits."""
    _exec("/scripts/sync-state")


@app.command()
def backup() -> None:
    """Snapshot /opt/data into /opt/data/backups/."""
    _exec("/scripts/backup")


if __name__ == "__main__":
    app()
