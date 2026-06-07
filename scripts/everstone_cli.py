#!/usr/bin/env python3
"""EverStone admin CLI — the operator-facing surface of the running container.

Invoked as `everstone <command>` from inside the container, or from the host
as `docker exec [-it] everstone everstone <command>`. The host-side Justfile
wraps these for dev convenience, but the in-container CLI is the source of
truth for what an operator can do at runtime.
"""
import os
import subprocess
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
    help="EverStone admin CLI. From the host: `just esadmin <command>` (or `docker exec [-it] everstone esadmin <command>`).",
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


def _provider_from_model(model: str) -> str:
    """Derive the provider from a `provider/model` spec (e.g. openai-codex/gpt-5.5)."""
    if "/" not in model:
        typer.echo(f"Model must be `provider/model` (e.g. openai-codex/gpt-5.5), got '{model}'.", err=True)
        raise SystemExit(1)
    return model.split("/", 1)[0]


# ─── model ─────────────────────────────────────────────────────────────────

@app.command()
def model(
    value: str = typer.Argument(..., help="LLM as provider/model, e.g. openai-codex/gpt-5.5 or anthropic/claude-opus-4."),
) -> None:
    """Set the LLM model + run its provider auth (one-time setup for the brain)."""
    provider = _provider_from_model(value)
    # 1) Set model + provider in the Hermes profile config.
    subprocess.run(["hermes", "-p", "everstone", "config", "set", "model", value], check=True)
    subprocess.run(["hermes", "-p", "everstone", "config", "set", "provider", provider], check=True)
    # 2) Run the provider's auth. openai-codex uses the OAuth manual-paste flow;
    #    other providers fall through to Hermes' interactive `auth add`.
    if provider == "openai-codex":
        _exec("hermes", "-p", "everstone", "auth", "add", "openai-codex", "--type", "oauth", "--manual-paste")
    else:
        _exec("hermes", "-p", "everstone", "auth", "add", provider)


# ─── auth ──────────────────────────────────────────────────────────────────

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


# ─── calendars ─────────────────────────────────────────────────────────────

@app.command()
def calendars() -> None:
    """List calendars the authed Google account can see — use this to discover IDs for config.yaml."""
    from es.google_auth import calendar_service
    svc = calendar_service()
    items = svc.calendarList().list().execute().get("items", [])
    for c in items:
        print(f"{c.get('accessRole','?'):>8}  {c.get('summary','')}  ({c.get('id')})")


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


_LOG_DIR = "/opt/data/hermes/profiles/everstone/logs"
_LONGRUNS = ("hermes", "caddy", "couchdb", "radicale", "livesync-bridge")


@app.command()
def logs(
    name: str = typer.Argument("gateway", help="Hermes log to tail: gateway, agent, errors."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow the log (tail -f)."),
    lines: int = typer.Option(80, "--lines", "-n", help="Number of lines to show."),
) -> None:
    """Tail a Hermes log (gateway, agent, errors)."""
    args = ["tail", f"-n{lines}"]
    if follow:
        args.append("-f")
    args.append(f"{_LOG_DIR}/{name}.log")
    _exec(*args)


@app.command()
def restart(
    service: str = typer.Argument("hermes", help="s6 service to restart."),
    hard: bool = typer.Option(
        False, "--hard", "-k",
        help="SIGKILL instead of graceful SIGTERM — for a wedged service.",
    ),
) -> None:
    """Restart an s6 service (default: hermes). It comes back automatically (longrun)."""
    if service not in _LONGRUNS:
        typer.echo(f"Unknown service '{service}'. Choose from: {', '.join(_LONGRUNS)}.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Restarting {service} (comes back automatically)…", err=True)
    _exec("s6-svc", "-k" if hard else "-t", f"/run/service/{service}")


if __name__ == "__main__":
    app()
