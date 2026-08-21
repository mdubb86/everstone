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
    default. Pass the already-loaded obsidian sub-config.

    production routes to paths.py/vault_client.py THROUGH this function, and
    both of those guard against a bare scalar string being iterated
    char-by-char (`roots="/x"` -> `["/", "x"]`, putting "/" in the allowlist)
    — but only if a scalar ever reaches them. A scalar `sources:` value in
    config.yaml would splat right here, before either guard runs. Not
    exploitable in a booted container (the config schema requires an array
    and configure.py refuses to boot otherwise), but normalize it anyway so
    this call site can't hand either guard a value it was never meant to see.
    """
    obs = obsidian or {}
    sources = (obs.get("attachments") or {}).get("sources")
    if isinstance(sources, (str, bytes)):
        sources = [sources]
    return list(sources or _DEFAULT_ATTACH_SOURCES)


def readable_source_dirs(obsidian=None) -> list:
    """Dirs the agent may READ documents from: the media cache plus the vault.
    Attachment sources stay separate — attach copies INTO the vault, so the
    vault is deliberately not an attach source."""
    return list(attach_source_dirs(obsidian)) + [str(vault_root())]


def maps_config(cfg=None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("maps") or {}


def weather_config(cfg=None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("weather") or {}
