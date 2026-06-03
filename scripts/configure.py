#!/usr/bin/env python3
"""
Everstone configuration generator.

Merges user config with defaults, validates against schema,
and generates all service config files.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import validate, ValidationError


DEFAULTS_CONFIG_DIR = Path("/opt/defaults/config")
DEFAULTS_PATH = DEFAULTS_CONFIG_DIR / "defaults.yaml"
SCHEMA_PATH = DEFAULTS_CONFIG_DIR / "schema.json"

CONFIG_DIR = Path("/opt/config")
DATA_DIR = Path("/opt/data")


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
    """Generate Caddyfile from template."""
    template_path = DEFAULTS_CONFIG_DIR / "caddy" / "Caddyfile"
    output_dir = CONFIG_DIR / "caddy"
    output_path = output_dir / "Caddyfile"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Hash the git password using caddy
    result = subprocess.run(
        ["caddy", "hash-password", "--plaintext", config["git"]["password"]],
        capture_output=True,
        text=True,
        check=True
    )
    password_hash = result.stdout.strip()

    template = template_path.read_text()
    result = template.replace("{{GIT_USER}}", config["git"]["user"])
    result = result.replace("{{GIT_PASSWORD_HASH}}", password_hash)

    output_path.write_text(result)


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


def setup_data_directories() -> None:
    """Create data directories with correct permissions."""

    # Initialize couchdb data directory if necessary
    couchdb_dir = DATA_DIR / "couchdb"
    if not couchdb_dir.exists():
        print("[configure] Initializing couchdb directory")
        couchdb_dir.mkdir()
        shutil.chown(couchdb_dir, user="couchdb", group="couchdb")

    # Initialize git repository if necessary
    git_dir = DATA_DIR / "git"
    if not git_dir.exists():
        print("[configure] Initializing git repository")
        git_dir.mkdir()
        git_repo = git_dir / "everstone.git"
        subprocess.run(["git", "init", "--bare", str(git_repo)], check=True)
        subprocess.run(["git", "config", "--file", str(git_repo / "config"), "http.receivepack", "true"], check=True)

    # Initialize radicale data directory if necessary
    radicale_dir = DATA_DIR / "radicale"
    if not radicale_dir.exists():
        print("[configure] Initializing radicale directory")
        radicale_dir.mkdir()

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

    print("[configure] Configuration complete")


if __name__ == "__main__":
    main()
