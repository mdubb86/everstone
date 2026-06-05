#!/usr/bin/env python3
"""
Everstone configuration generator.

Merges user config with defaults, validates against schema,
and generates all service config files.
"""

import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

import yaml
from jsonschema import validate, ValidationError


DEFAULTS_CONFIG_DIR = Path(os.environ.get("EVERSTONE_DEFAULTS_DIR", "/opt/defaults/config"))
SCHEMA_PATH = DEFAULTS_CONFIG_DIR / "schema.json"
DEFAULTS_PATH = DEFAULTS_CONFIG_DIR / "defaults.yaml"
CONFIG_DIR = Path(os.environ.get("EVERSTONE_CONFIG_DIR", "/opt/config"))
DATA_DIR = Path(os.environ.get("EVERSTONE_DATA_DIR", "/opt/data"))


def _config_dir() -> Path:
    return Path(os.environ.get("EVERSTONE_CONFIG_DIR", "/opt/config"))


def _data_dir() -> Path:
    return Path(os.environ.get("EVERSTONE_DATA_DIR", "/opt/data"))


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base, returning new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def generate_couchdb_config(config: dict) -> None:
    """Generate CouchDB local.ini from template."""
    template_path = DEFAULTS_CONFIG_DIR / "couchdb" / "local.ini"
    output_dir = CONFIG_DIR / "couchdb"
    output_path = output_dir / "local.ini"

    output_dir.mkdir(parents=True, exist_ok=True)

    template = template_path.read_text()
    result = template.replace("{{COUCHDB_USER}}", config["couchdb"]["user"])
    result = result.replace("{{COUCHDB_PASSWORD}}", config["couchdb"]["password"])

    output_path.write_text(result)

    # Set ownership to couchdb user
    shutil.chown(output_dir, user="couchdb", group="couchdb")
    shutil.chown(output_path, user="couchdb", group="couchdb")


def generate_caddy_config(config: dict) -> None:
    """Copy Caddyfile template (no substitution needed)."""
    template_path = DEFAULTS_CONFIG_DIR / "caddy" / "Caddyfile"
    output_dir = _config_dir() / "caddy"
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(template_path, output_dir / "Caddyfile")


def generate_setup_livesync_script(config: dict) -> None:
    """Generate the setup-obsidian-livesync script with injected values.

    Bakes the live CouchDB credentials AND the LiveSync passphrase into the
    template at container-startup time, so running the script needs no prompts:
        docker exec everstone setup-obsidian-livesync
    """
    template_path = DEFAULTS_CONFIG_DIR / "setup-obsidian-livesync"
    output_path = Path(os.environ.get("EVERSTONE_SETUP_LIVESYNC_PATH", "/scripts/setup-obsidian-livesync"))

    template = template_path.read_text()
    result = template.replace("{{COUCHDB_USER}}", config["couchdb"]["user"])
    result = result.replace("{{COUCHDB_PASSWORD}}", config["couchdb"]["password"])
    result = result.replace("{{COUCHDB_DATABASE}}", config["couchdb"]["database"])
    result = result.replace("{{LIVESYNC_PASSPHRASE}}", config["livesync"]["passphrase"])
    result = result.replace("{{PUBLIC_URL}}", config["public_url"].rstrip("/"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result)
    output_path.chmod(0o744)


_SOUL_TOKEN_RE = re.compile(r"<([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)>")


def render_soul_template(template: str, config: dict) -> str:
    """Substitute `<a.b.c>` tokens in `template` with values from `config`.

    Unknown / unresolvable tokens are left as-is so the user can see what
    went wrong instead of silently losing content.
    """
    def lookup(path: str):
        node = config
        for key in path.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return None
        return node if isinstance(node, (str, int, float)) else None

    def replace(match):
        val = lookup(match.group(1))
        return str(val) if val is not None else match.group(0)

    return _SOUL_TOKEN_RE.sub(replace, template)


def generate_hermes_soul(config: dict) -> None:
    """Render `agent.soul` from config and write it to $HERMES_HOME/SOUL.md.

    Always overwrites: config.yaml is the source of truth for the persona.
    To customize, edit `agent.soul` in config.yaml and restart the container.
    """
    data_dir = _data_dir()
    hermes_dir = data_dir / "hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)
    rendered = render_soul_template(config["agent"]["soul"], config)
    (hermes_dir / "SOUL.md").write_text(rendered)


# Platform section of AGENTS.md. Container-architectural truth — not
# operator-overridable. `<...>` tokens get pre-substituted at render time
# so the resulting file reads cleanly without templating noise.
_AGENTS_PLATFORM_TEMPLATE = """\
## EverStone platform

You are running inside EverStone, <name>'s self-hosted personal hub.
Everything below is fact about your environment — not stylistic guidance
(that lives in SOUL.md).

### Notes — Obsidian vault

- Plaintext markdown files live at `/opt/data/vault/`.
- Read with `read_file` (or `cat`), edit with `write_file` / `Edit`.
  Every change there propagates to <name>'s Obsidian within ~1s via
  the LiveSync bridge.
- Search with `grep -rni "<pattern>" /opt/data/vault/` or `find` for
  filename matches. Don't search outside `/opt/data/vault/`.
- The Obsidian vault name is `<obsidian.vault_name>`. When you create
  task deeplinks, use:
    `obsidian://open?vault=<obsidian.vault_name>&file=<url-encoded-path>`

### Tasks — CalDAV

- Use the `everstone-tasks` CLI for all task operations. Invoke it via
  the terminal/shell tool. There is no MCP for tasks — the CLI is the
  whole interface.
- Examples:
    everstone-tasks add "Buy milk" --list inbox
    everstone-tasks add "Review Q4 plan" --list inbox --note "Projects/Q4.md"
    everstone-tasks list --list inbox --json
    everstone-tasks done <uid> --list inbox
- Run `everstone-tasks --help` for the full surface.
- Tasks can deeplink to notes using the obsidian:// URL above.

### Who is who

- The user's name is <name>. Address them by name when natural;
  don't force it.

### Chat-context constraints

- In <name>'s private DM you have your full configured tool set.
- In any group chat the access policy enforces tasks-only — the only
  permitted shell invocation is a single `everstone-tasks ...` call
  (no pipes, no chaining, no other binaries). Other tool calls and
  file ops fail at the runtime layer. Don't apologize for the
  restriction; it's structural.

### Chat voice

- You are an assistant texting a person, not a sysadmin reading off
  a tool inventory. Replies are concise and specific to the user's
  request. When greeted ("hi", "/start", etc.), reply briefly — e.g.
  "Hey <name>, what's up?" — and wait for the actual request.
- When you have to use shell/terminal, just do it; don't narrate the
  commands unless asked how you did something.
- If asked "what can you do," frame the answer around assistant tasks
  (notes, tasks, search, reminders, web research) — not the underlying
  tool list.
"""


def _render_calendar_section(config: dict) -> str:
    """Return the Calendar section for AGENTS.md, or '' if gcalcli unconfigured.

    The section names the read-only vs read-write calendars from
    config.gcalcli.calendars so the agent has explicit, visible policy.
    Soft enforcement — gcalcli itself has no per-calendar permissions
    (Google API is all-or-nothing on the calendar scope), so the agent
    self-respects via these instructions.
    """
    gcalcli = config.get("gcalcli")
    if not gcalcli:
        return ""
    cals = gcalcli.get("calendars") or {}
    ro = cals.get("read_only") or []
    rw = cals.get("read_write") or []
    if not ro and not rw:
        return ""

    def _bulleted(items):
        return "\n".join(f"  - `{c}`" for c in items) if items else "  - (none)"

    return f"""\
### Calendar — Google Calendar via `gcal`

You have read/write Google Calendar access via the `gcal` CLI wrapper
(thin pre-configured wrapper around `gcalcli`). Examples:

    gcal agenda                              # what's coming up
    gcal quick "lunch with Sarah Friday 12pm"  # natural-language add
    gcal add "1:1 with Alex" --when "2026-06-10 14:00" --calendar work@example.com
    gcal search "dentist"
    gcal --help                              # full surface

Run `gcal --help` for the full surface (same as `gcalcli --help`).

Calendar policy — enforced by YOU, not the tool:

READ-ONLY (look, never modify):
{_bulleted(ro)}

READ-WRITE (add/edit/delete freely):
{_bulleted(rw)}

ANY OTHER CALENDAR: do not touch — refuse and ask the user to
configure it in `config.yaml` if they want access. When a request is
ambiguous about which calendar to use ("add a meeting tomorrow"),
confirm the calendar before writing.
"""


def generate_agents_md(config: dict) -> None:
    """Render /opt/data/AGENTS.md from platform truth + agent.instructions.

    The file is in two sections:
      ## EverStone platform           — auto-generated, always present
      ## Custom instructions          — operator-authored, optional

    Always overwrites: config.yaml is the source of truth. Manual edits
    to AGENTS.md are lost on next container restart. To change the file,
    edit `agent.instructions` in config.yaml.

    Hermes finds this file by scanning TERMINAL_CWD for AGENTS.md, so the
    hermes envdir also exports HERMES_TERMINAL_CWD=/opt/data (handled in
    generate_hermes_env).
    """
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    platform = render_soul_template(_AGENTS_PLATFORM_TEMPLATE, config)
    calendar = _render_calendar_section(config)
    instructions = (config.get("agent") or {}).get("instructions")
    parts = [platform.rstrip()]
    if calendar:
        parts.append(calendar.rstrip())
    if isinstance(instructions, str) and instructions.strip():
        rendered_instructions = render_soul_template(instructions, config)
        parts.append("## Custom instructions\n\n" + rendered_instructions.strip())
    (data_dir / "AGENTS.md").write_text("\n\n".join(parts) + "\n")


def generate_radicale_config(config: dict) -> None:
    """Copy radicale config template and write htpasswd from config."""
    config_dir = _config_dir()
    output_dir = config_dir / "radicale"
    output_dir.mkdir(parents=True, exist_ok=True)
    template_path = DEFAULTS_CONFIG_DIR / "radicale" / "config"
    if template_path.exists():
        shutil.copy(template_path, output_dir / "config")
    htpasswd_path = output_dir / "htpasswd"
    htpasswd_path.write_text(f"{config['caldav']['user']}:{config['caldav']['password']}\n")


def generate_livesync_bridge_config(config: dict) -> None:
    """Generate livesync-bridge config.json from config.

    Schema matches livesync-bridge's `dat/config.sample.json`: peers need
    `name`, CouchDB peers use `username` (not `user`) and require a `baseDir`.
    """
    config_dir = _config_dir()
    output_dir = config_dir / "livesync-bridge"
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "peers": [
            {
                "type": "couchdb",
                "name": "couchdb",
                "group": "everstone",
                "url": "http://localhost:5984",
                "database": config["couchdb"]["database"],
                "username": config["couchdb"]["user"],
                "password": config["couchdb"]["password"],
                # Per livesync upstream (lib/src/cli/APITest.sample.ts): the bridge's
                # obfuscatePassphrase "should be the same as passphrase". The plugin
                # has only one setting key (`passphrase`) and uses it for both
                # content encryption and path obfuscation.
                "passphrase": config["livesync"]["passphrase"],
                "obfuscatePassphrase": config["livesync"]["passphrase"],
                "baseDir": "",
            },
            {
                "type": "storage",
                "name": "vault",
                "group": "everstone",
                "baseDir": "/opt/data/vault/",
            },
        ]
    }
    output_path = output_dir / "config.json"
    output_path.write_text(json.dumps(cfg, indent=2))


def generate_gcalcli_client_secret(config: dict) -> str:
    """Materialize an OAuth client_secret.json for gcalcli from config values.

    Google's `gcalcli` reads the standard installed-app JSON format. We build
    it from operator-supplied client_id + client_secret, with Google's
    constant auth/token endpoints hardcoded. Written under the CONFIG dir
    (not DATA) — it's regenerated every boot from config.yaml, so persistence
    is unnecessary. The OAuth refresh token gcalcli mints after first auth
    DOES persist (lives under DATA at /opt/data/hermes/gcalcli/).

    Returns the absolute path to the written file, or '' when gcalcli is not
    configured (so generate_hermes_env can leave GCALCLI_CLIENT_SECRET empty).
    """
    gcalcli = config.get("gcalcli")
    if not gcalcli:
        return ""

    client_id = gcalcli.get("client_id")
    client_secret = gcalcli.get("client_secret")
    if not client_id or not client_secret:
        return ""

    config_dir = _config_dir()
    out_dir = config_dir / "hermes" / "gcalcli"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "client_secret.json"

    # Standard installed-application form. auth_uri / token_uri are constants
    # for Google OAuth — same for every client_id. redirect_uris matches what
    # `gcalcli --noauth_local_server` expects for the paste-back flow.
    payload = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost", "urn:ietf:wg:oauth:2.0:oob"],
        }
    }
    out_path.write_text(json.dumps(payload, indent=2))
    out_path.chmod(0o600)
    return str(out_path)


def generate_hermes_env(config: dict) -> None:
    """Generate s6 envdir AND sourceable env file for hermes from config."""
    config_dir = _config_dir()
    hermes_dir = config_dir / "hermes"
    envdir = hermes_dir / "envdir"
    envdir.mkdir(parents=True, exist_ok=True)

    owner_id = str(config["telegram"]["owner_user_id"])
    env_vars = {
        "EVERSTONE_CALDAV_URL": "http://localhost:5232",
        "EVERSTONE_CALDAV_USER": config["caldav"]["user"],
        "EVERSTONE_CALDAV_PASSWORD": config["caldav"]["password"],
        "EVERSTONE_VAULT_NAME": config["obsidian"]["vault_name"],
        "EVERSTONE_AGENT_NAME": config["agent"]["name"],
        "EVERSTONE_OWNER_NAME": config["name"],
        "HERMES_MODEL": config["hermes"]["model"],
        "TELEGRAM_BOT_TOKEN": config["telegram"]["bot_token"],
        "TELEGRAM_OWNER_USER_ID": owner_id,
        "TELEGRAM_ALLOWED_USERS": owner_id,
        "EVERSTONE_GROUP_BINARIES": "everstone-tasks",
        # Hermes scans TERMINAL_CWD for AGENTS.md / .cursorrules / context
        # files. /opt/data is where configure.py drops the generated
        # AGENTS.md, so the agent picks it up automatically on startup.
        "HERMES_TERMINAL_CWD": "/opt/data",
        # Telegram setMyCommands payload. Default "[]" → no autocomplete
        # clutter. setup_hermes posts this to the Bot API at startup.
        "TELEGRAM_COMMANDS": json.dumps([
            {"command": c["cmd"], "description": c["desc"]}
            for c in (config["telegram"].get("commands") or [])
        ]),
        # Optional GitHub PAT — exposed only when set, so setup_hermes can
        # branch on its presence to wire the git credential helper. Empty
        # string means "not configured" (git stays unauth'd, falls back to
        # public clones only).
        "GH_TOKEN": (config.get("github") or {}).get("token") or "",
        # Space-separated list of Hermes skill names to install at boot.
        # Default empty = clean ship; setup_hermes loops and installs each.
        "EVERSTONE_SKILLS": " ".join(
            (config.get("agent") or {}).get("skills") or []
        ),
        # gcalcli wiring — empty strings = "not configured" so the
        # everstone CLI / wrapper / setup_hermes can branch cleanly.
        # The client_secret.json is generated under /opt/config from
        # config.yaml's gcalcli.{client_id, client_secret}; the path
        # below points at that generated file (or '' when unconfigured).
        "GCALCLI_CLIENT_SECRET": generate_gcalcli_client_secret(config),
        "EVERSTONE_GCAL_READ_ONLY": " ".join(
            ((config.get("gcalcli") or {}).get("calendars") or {}).get("read_only") or []
        ),
        "EVERSTONE_GCAL_READ_WRITE": " ".join(
            ((config.get("gcalcli") or {}).get("calendars") or {}).get("read_write") or []
        ),
    }
    for name, value in env_vars.items():
        (envdir / name).write_text(value)

    env_file = hermes_dir / "env"
    env_file.write_text(
        "\n".join(f'export {k}={shlex.quote(v)}' for k, v in env_vars.items()) + "\n"
    )


def setup_data_directories() -> None:
    """Create data directories with correct permissions."""
    data_dir = _data_dir()

    # Initialize couchdb data directory if necessary
    couchdb_dir = data_dir / "couchdb"
    if not couchdb_dir.exists():
        print("[configure] Initializing couchdb directory")
        couchdb_dir.mkdir(parents=True, exist_ok=True)
        shutil.chown(couchdb_dir, user="couchdb", group="couchdb")

    # Initialize radicale data directory if necessary
    radicale_dir = data_dir / "radicale"
    if not radicale_dir.exists():
        print("[configure] Initializing radicale directory")
        radicale_dir.mkdir(parents=True, exist_ok=True)

    # Initialize vault data directory
    vault_dir = data_dir / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Initialize hermes data directory
    hermes_dir = data_dir / "hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)

    # Initialize radicale/collections data directory
    radicale_collections_dir = data_dir / "radicale" / "collections"
    radicale_collections_dir.mkdir(parents=True, exist_ok=True)

def main():
    user_config_path = Path("/opt/config.yaml")

    # Load defaults
    defaults = load_yaml(DEFAULTS_PATH)

    # Load user config
    if not user_config_path.exists():
        print(f"Error: User config not found at {user_config_path}", file=sys.stderr)
        sys.exit(1)

    user_config = load_yaml(user_config_path)

    # Merge configs
    config = deep_merge(defaults, user_config)

    # Validate against schema
    schema = load_json(SCHEMA_PATH)
    try:
        validate(instance=config, schema=schema)
    except ValidationError as e:
        print(f"Config validation error: {e.message}", file=sys.stderr)
        sys.exit(1)

    print("[configure] Config validated successfully")

    # Setup data directories
    print("[configure] Setting up data directories")
    setup_data_directories()

    # Generate service configs
    print("[configure] Generating CouchDB config")
    generate_couchdb_config(config)

    print("[configure] Generating Caddy config")
    generate_caddy_config(config)

    print("[configure] Generating setup-obsidian-livesync script")
    generate_setup_livesync_script(config)

    print("[configure] Generating radicale config")
    generate_radicale_config(config)

    print("[configure] Generating livesync-bridge config")
    generate_livesync_bridge_config(config)

    print("[configure] Generating hermes envdir")
    generate_hermes_env(config)

    print("[configure] Rendering Hermes SOUL.md from config.agent.soul")
    generate_hermes_soul(config)

    print("[configure] Rendering AGENTS.md from platform + config.agent.instructions")
    generate_agents_md(config)

    print("[configure] Configuration complete")


if __name__ == "__main__":
    main()
