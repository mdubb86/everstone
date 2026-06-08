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
