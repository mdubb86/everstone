import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("configure", ROOT/"scripts"/"configure.py")
configure = importlib.util.module_from_spec(spec); spec.loader.exec_module(configure)

SAMPLE = {
  "couchdb": {"user":"u","password":"p","database":"vault"},
  "caldav": {"user":"cu","password":"cp"},
  "livesync": {"passphrase":"ph","obfuscate_passphrase":"ob"},
  "obsidian": {"vault_name":"myvault"}, "instance": {"name":"Jarvis"},
  "telegram": {"owner_user_id":111,"bot_token":"TKN"}, "hermes": {"model":"openai/gpt-5-codex"},
}

def test_deep_merge():
    assert configure.deep_merge({"a":{"x":1,"y":2}}, {"a":{"y":9}}) == {"a":{"x":1,"y":9}}

import json, os, tempfile
from pathlib import Path

def test_generate_radicale_htpasswd(tmp_path):
    os.environ["EVERSTONE_CONFIG_DIR"] = str(tmp_path)
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path / "data")
    try:
        configure.generate_radicale_config(SAMPLE)
        htpasswd = (tmp_path / "radicale" / "htpasswd").read_text()
        assert "cu:cp" in htpasswd
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]
        del os.environ["EVERSTONE_DATA_DIR"]

def test_generate_livesync_bridge_config(tmp_path):
    os.environ["EVERSTONE_CONFIG_DIR"] = str(tmp_path)
    try:
        configure.generate_livesync_bridge_config(SAMPLE)
        cfg = json.loads((tmp_path / "livesync-bridge" / "config.json").read_text())
        peers = cfg["peers"]
        couchdb_peer = next(p for p in peers if p.get("type") == "couchdb")
        storage_peer = next(p for p in peers if p.get("type") == "storage")
        assert couchdb_peer["database"] == "vault"
        assert couchdb_peer["username"] == "u"
        assert couchdb_peer["passphrase"] == "ph"
        assert "name" in couchdb_peer and "name" in storage_peer
        assert storage_peer["baseDir"] == "/opt/data/vault/"
        assert couchdb_peer["group"] == storage_peer["group"]
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]

def test_generate_hermes_env(tmp_path):
    os.environ["EVERSTONE_CONFIG_DIR"] = str(tmp_path)
    try:
        configure.generate_hermes_env(SAMPLE)
        envdir = tmp_path / "hermes" / "envdir"
        assert (envdir / "EVERSTONE_CALDAV_URL").read_text() == "http://localhost:5232"
        assert (envdir / "EVERSTONE_CALDAV_USER").read_text() == "cu"
        assert (envdir / "EVERSTONE_CALDAV_PASSWORD").read_text() == "cp"
        assert (envdir / "EVERSTONE_VAULT_NAME").read_text() == "myvault"
        assert (envdir / "EVERSTONE_AGENT_NAME").read_text() == "Jarvis"
        assert (envdir / "HERMES_MODEL").read_text() == "openai/gpt-5-codex"
        assert (envdir / "TELEGRAM_BOT_TOKEN").read_text() == "TKN"
        assert (envdir / "TELEGRAM_OWNER_USER_ID").read_text() == "111"
        assert (envdir / "TELEGRAM_ALLOWED_USERS").read_text() == "111"
        assert (envdir / "EVERSTONE_GROUP_TOOLS").read_text() == "everstone_tasks"
        # sourceable env file for setup_hermes
        env_file = (tmp_path / "hermes" / "env").read_text()
        assert "export TELEGRAM_ALLOWED_USERS=111" in env_file
        assert "export HERMES_MODEL=openai/gpt-5-codex" in env_file
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]
