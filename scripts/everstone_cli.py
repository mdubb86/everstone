#!/usr/bin/env python3
"""EverStone admin CLI — the operator-facing surface of the running container.

Invoked as `everstone <command>` from inside the container, or from the host
as `docker exec [-it] everstone everstone <command>`. The host-side Justfile
wraps these for dev convenience, but the in-container CLI is the source of
truth for what an operator can do at runtime.
"""
import os

import typer

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
