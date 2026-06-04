#!/usr/bin/env python3
"""
Everstone configuration generator.

Merges user config with defaults, validates against schema,
and generates all service config files.
"""

import json
import os
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


def generate_setupuri_script(config: dict) -> None:
    """Generate setupuri script with injected config values."""
    template_path = DEFAULTS_CONFIG_DIR / "setupuri"
    output_path = Path("/scripts/setupuri")

    template = template_path.read_text()
    result = template.replace("{{COUCHDB_USER}}", config["couchdb"]["user"])
    result = result.replace("{{COUCHDB_PASSWORD}}", config["couchdb"]["password"])
    result = result.replace("{{COUCHDB_DATABASE}}", config["couchdb"]["database"])

    output_path.write_text(result)
    output_path.chmod(0o744)


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
    """Generate livesync-bridge config.json from config."""
    config_dir = _config_dir()
    output_dir = config_dir / "livesync-bridge"
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "peers": [
            {
                "type": "couchdb",
                "url": "http://localhost:5984",
                "database": config["couchdb"]["database"],
                "user": config["couchdb"]["user"],
                "password": config["couchdb"]["password"],
                "passphrase": config["livesync"]["passphrase"],
                "obfuscatePassphrase": config["livesync"]["obfuscate_passphrase"],
                "group": "everstone",
            },
            {
                "type": "storage",
                "baseDir": "/opt/data/vault/",
                "group": "everstone",
            },
        ]
    }
    output_path = output_dir / "config.json"
    output_path.write_text(json.dumps(cfg, indent=2))


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
        "EVERSTONE_AGENT_NAME": config["instance"]["name"],
        "HERMES_MODEL": config["hermes"]["model"],
        "TELEGRAM_BOT_TOKEN": config["telegram"]["bot_token"],
        "TELEGRAM_OWNER_USER_ID": owner_id,
        "TELEGRAM_ALLOWED_USERS": owner_id,
        "EVERSTONE_GROUP_TOOLS": "everstone_tasks",
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

    print("[configure] Generating setupuri script")
    generate_setupuri_script(config)

    print("[configure] Generating radicale config")
    generate_radicale_config(config)

    print("[configure] Generating livesync-bridge config")
    generate_livesync_bridge_config(config)

    print("[configure] Generating hermes envdir")
    generate_hermes_env(config)

    print("[configure] Configuration complete")


if __name__ == "__main__":
    main()
