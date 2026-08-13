"""Config access for es. Reads the mounted /opt/config.yaml directly (no
envdir). Derived constants that configure.py used to inject live here."""
import os
from pathlib import Path

import yaml

# In-container Radicale CalDAV endpoint — a derived constant, not in config.yaml.
CALDAV_URL = "http://localhost:5232"


def _config_path() -> Path:
    return Path(os.environ.get("ES_CONFIG_PATH", "/opt/config.yaml"))


def load_config() -> dict:
    path = _config_path()
    if not path.is_file():
        raise FileNotFoundError(f"es: config not found at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"es: config at {path} is not a mapping")
    return data


def vault_root() -> Path:
    """Obsidian vault root. Defaults to the in-container /opt/data/vault;
    override with ES_VAULT_PATH (tests point this at a tmp dir)."""
    return Path(os.environ.get("ES_VAULT_PATH", "/opt/data/vault"))


# Directories es_notes_attach may copy files FROM. Default: the Hermes media
# cache, where Telegram uploads + agent-generated media land. Secrets live in
# the profile root (config.yaml, .env, es/), OUTSIDE cache/, so they're excluded.
# Override (rarely needed) with obsidian.attachments.sources in config.yaml.
_DEFAULT_ATTACH_SOURCES = ["/opt/data/hermes/profiles/everstone/cache"]


def attach_source_dirs(obsidian=None) -> list:
    """Allowed attachment source dirs, from obsidian.attachments.sources or the
    default. Pass the already-loaded obsidian sub-config."""
    obs = obsidian or {}
    return list(((obs.get("attachments") or {}).get("sources")) or _DEFAULT_ATTACH_SOURCES)


def maps_config(cfg=None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("maps") or {}


def weather_config(cfg=None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("weather") or {}
