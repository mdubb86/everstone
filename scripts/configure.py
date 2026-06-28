#!/usr/bin/env python3
"""
Everstone configuration generator.

Merges user config with defaults, validates against schema,
and generates all service config files.
"""

import json
import os
import re
import shutil
import sys
import urllib.request
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
    """Render `agent.soul` from config and write it to the active Hermes
    profile's SOUL.md.

    The gateway runs `hermes -p everstone`, and a profile loads its OWN
    `profiles/<name>/SOUL.md` — NOT `$HERMES_HOME/SOUL.md`. Writing to the
    profile dir is what actually makes the persona take effect; the global
    file is shadowed by the profile's and silently ignored.

    Always overwrites: config.yaml is the source of truth for the persona.
    To customize, edit `agent.soul` in config.yaml and restart the container.
    """
    profile = os.environ.get("HERMES_PROFILE", "everstone")
    profile_dir = _data_dir() / "hermes" / "profiles" / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    rendered = render_soul_template(config["agent"]["soul"], config)
    (profile_dir / "SOUL.md").write_text(rendered)


# Platform section of AGENTS.md. Container-architectural truth — not
# operator-overridable. `<...>` tokens get pre-substituted at render time
# so the resulting file reads cleanly without templating noise.
_AGENTS_PLATFORM_TEMPLATE = """\
## EverStone platform

You are running inside EverStone, <name>'s self-hosted personal hub.
Everything below is fact about your environment — not stylistic guidance
(that lives in SOUL.md).

### Your tools

You act through a fixed set of tools — there is no shell and no raw file
access. Your EverStone capabilities:
- **Tasks** — the `es_tasks_*` tools (list, add, edit, done, delete, lists,
  list_create, list_delete, clear). CalDAV-backed.
- **Calendar** — the `es_cal_*` tools (agenda, search, conflicts, add, edit,
  delete). (Present only if Google Calendar is configured.)
- **Notes** — the `es_notes_*` tools (journal, topic, topics, read, list) over
  <name>'s Obsidian vault.
- **Contacts** — the `es_contacts_search` tool: look up someone's phone, email,
  or address from <name>'s Google contacts (read-only; DM only).
Each tool returns a JSON object: check `ok`, then read `data` (or `error.code`).

### Tasks

- Use the `es_tasks_*` tools for all task operations; the default list is `TODO`.
- **Reminders ("remind me to ...") are due-dated TODOs, NEVER crons.** Call
  `es_tasks_add(summary=..., due=..., remind=...)` so the reminder lands on
  <name>'s list AND their app notifies them. Do NOT use the cronjob tool for a
  user reminder — cron only schedules actions the AGENT performs and leaves
  nothing on the user's task list. "remind me to X" is always a due-dated TODO.

### Notes — Obsidian vault

- Capture notes with the `es_notes_*` tools — you do not read or write the vault
  as files. Journal entries are atomic (one per thought); topics are curated
  docs. The `note-taking` skill carries the journal-vs-topic routing.
- The Obsidian vault name is `<obsidian.vault_name>`. The note tools return an
  `obsidian_deeplink` you can share with <name>.

### Web research

- To **find** information or a source you don't already have, use `web_search` —
  it returns results and URLs.
- To **read** a specific page you already have a URL for (including a `web_search`
  result), use `es_web_fetch(url)` — a fast, light read of the page text.
- **Escalate to the browser** (`browser_*`) when `es_web_fetch` errors or comes
  back thin/empty (JavaScript-rendered, paywalled, login-gated), or when the task
  needs interaction (clicking, forms, multi-step). The browser is heavier — use it
  only when the light read isn't enough.
- **Never fabricate web facts.** State only what you actually retrieved from a tool
  result, and attribute facts only to sources you actually opened. If the web tools
  error or return nothing, say you couldn't reach the web — do NOT answer factual
  queries (showtimes, prices, hours, availability, news, scores) from memory. If you
  have only search snippets but couldn't open the authoritative/live page, present
  them as **unverified** ("from search results, not confirmed on the live page"),
  never as established fact.

### Who is who

- The user's name is <name>. Address them by name when natural;
  don't force it.

### Chat-context constraints

- In <name>'s private DM you have your full tool set.
- In any group chat the access policy permits only the task and calendar tools
  (`es_tasks_*`, `es_cal_*`); notes and every other tool are blocked at the
  runtime layer. Don't apologize for the restriction; it's structural.

### Chat voice

- You are an assistant texting a person, not a sysadmin reading off a tool
  inventory. Replies are concise and specific to the user's request. When
  greeted ("hi", "/start", etc.), reply briefly — e.g. "Hey <name>, what's up?"
  — and wait for the actual request.
- Just use your tools; don't narrate the calls unless asked how you did something.
- If asked "what can you do," frame the answer around assistant tasks (notes,
  tasks, calendar, reminders, web research) — not the underlying tool list.
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
### Calendar — Google Calendar via the `es_cal_*` tools

You have Google Calendar access through the `es_cal_*` tools. Each returns a
JSON object (check `ok`). Common calls:

    es_cal_agenda(start="<YYYY-MM-DD>", end="<YYYY-MM-DD>", calendar="<Name>")
    es_cal_add(summary="...", calendar="<Name>", when="YYYY-MM-DD HH:MM", duration=60, where="...")
    es_cal_search(query="dentist", calendar="<Name>")
    es_cal_edit(event_id="...", calendar="<Name>", ...)   es_cal_delete(event_id="...", calendar="<Name>")

Pass `calendar="Display Name"` to target a calendar. When <name> is away from
home, pass `tz="<IANA>"` (e.g. `tz="Europe/Oslo"`) so times are interpreted in
the right zone.

Calendar policy (read-only / read-write) for this user:

READ-ONLY:
{_bulleted(ro)}

READ-WRITE:
{_bulleted(rw)}

Read-only calendars are enforced by the tools — a write against one returns
`{{"ok": false, "error": {{"code": "read_only_calendar"}}}}`. Don't work around
it; explain the policy and suggest a read-write calendar. Calendars NOT in either
list above are off-limits — ask <name> to add them in `config.yaml` for agent
access.

When a request is ambiguous about which calendar to use ("add a meeting
tomorrow"), confirm before writing.
"""


def generate_agents_md(config: dict) -> None:
    """Render /opt/data/AGENTS.md from platform truth + agent.instructions.

    The file is in two sections:
      ## EverStone platform           — auto-generated, always present
      ## Custom instructions          — operator-authored, optional

    Always overwrites: config.yaml is the source of truth. Manual edits
    to AGENTS.md are lost on next container restart. To change the file,
    edit `agent.instructions` in config.yaml.

    Hermes finds this file by scanning TERMINAL_CWD for AGENTS.md; the
    hermes run script exports HERMES_TERMINAL_CWD=/opt/data directly.
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
                # Chunking/E2EE tweaks MUST match the plugin's settings (the conf
                # in config/setup-obsidian-livesync, which the plugins adopt as
                # their tweak_values). The bridge reads these only from its config
                # — never from the remote tweak_values — and otherwise falls back
                # to library defaults (customChunkSize 0, chunkSplitterVersion "").
                # A mismatch makes the bridge split/hash notes differently than the
                # plugins (e.g. a 1.7 KB note into 22 tiny chunks), breaking chunk
                # dedup and round-trips. Keep this block in sync with that script.
                "customChunkSize": 60,
                "minimumChunkSize": 20,
                "chunkSplitterVersion": "v3-rabin-karp",
                "hashAlg": "xxhash64",
                "E2EEAlgorithm": "v2",
                "useEden": False,
                "enableCompression": False,
                "handleFilenameCaseSensitive": False,
                "doNotUseFixedRevisionForChunks": True,
                "useDynamicIterationCount": False,
            },
            {
                "type": "storage",
                "name": "vault",
                "group": "everstone",
                "baseDir": "/opt/data/vault/",
                # Reconcile the on-disk vault against CouchDB on every (re)start.
                # Defaults off (the bridge logs "[vault] Scan offline changes:
                # Disabled"); without it, any file the live watcher missed — e.g. a
                # note es-notes wrote while the bridge was stalled/down — is never
                # pushed, and a restart won't recover it. With it on, startup does a
                # filesystem walk() and pushes anything CouchDB is missing.
                "scanOfflineChanges": True,
                # Use chokidar instead of Deno's native recursive watcher. The native
                # watcher is unreliable for subdirectories created AFTER the watch
                # starts, and es-notes creates them constantly (journal/YYYY-MM-DD/ is
                # new every day, topics/ on first note), so live writes into a fresh
                # subdir get silently dropped. chokidar watches new subdirs correctly.
                # (Statically imported by the bridge, so it's pre-cached in the image.)
                "useChokidar": True,
            },
        ]
    }
    output_path = output_dir / "config.json"
    output_path.write_text(json.dumps(cfg, indent=2))


def _telegram_commands(config: dict) -> list:
    """Build the Bot API setMyCommands payload from config.telegram.commands."""
    return [
        {"command": c["cmd"], "description": c["desc"]}
        for c in (config["telegram"].get("commands") or [])
    ]


def set_telegram_commands(config: dict) -> None:
    """POST setMyCommands to the Telegram Bot API. Best-effort: a failure here
    (network/token) logs a warning but does not abort boot. (Moved out of
    setup_hermes as part of envdir retirement.)"""
    token = config["telegram"]["bot_token"]
    commands = _telegram_commands(config)
    body = json.dumps({"commands": commands}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setMyCommands",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[configure] Telegram slash-commands set (count: {len(commands)}).")
    except Exception as e:  # noqa: BLE001 — best-effort, never block boot
        print(f"[configure] WARN: setMyCommands failed ({e}). Bot still functional.")


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

def load_config() -> dict:
    """Load defaults + user config.yaml, deep-merge, and validate against schema.

    Shared by main() and scripts/render_soul.py so the soul render reads exactly
    the same merged + validated config the rest of configure.py does.
    """
    user_config_path = Path("/opt/config.yaml")
    defaults = load_yaml(DEFAULTS_PATH)
    if not user_config_path.exists():
        print(f"Error: User config not found at {user_config_path}", file=sys.stderr)
        sys.exit(1)
    user_config = load_yaml(user_config_path)
    config = deep_merge(defaults, user_config)
    schema = load_json(SCHEMA_PATH)
    try:
        validate(instance=config, schema=schema)
    except ValidationError as e:
        print(f"Config validation error: {e.message}", file=sys.stderr)
        sys.exit(1)
    return config


def main():
    config = load_config()
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

    # NOTE: SOUL.md is rendered by setup_hermes (via render_soul.py) AFTER its
    # canonical `hermes profile create --no-skills`. configure.py must NOT touch
    # profiles/<name>/ here — a pre-emptive mkdir would win the race against that
    # create and leave the profile without the bundled-skill opt-out marker, so
    # the gateway would seed the full stock bundle.

    print("[configure] Rendering AGENTS.md from platform + config.agent.instructions")
    generate_agents_md(config)

    print("[configure] Registering Telegram slash-commands via Bot API")
    set_telegram_commands(config)

    print("[configure] Configuration complete")


if __name__ == "__main__":
    main()
