import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("configure", ROOT/"scripts"/"configure.py")
configure = importlib.util.module_from_spec(spec); spec.loader.exec_module(configure)

SAMPLE = {
  "public_url": "https://example.com",
  "name": "Michael",
  "agent": {"name": "Jarvis", "soul": "I am <agent.name>, in <name>'s hub. Vault: <obsidian.vault_name>."},
  "couchdb": {"user":"u","password":"p","database":"vault"},
  "caldav": {"user":"cu","password":"cp"},
  "livesync": {"passphrase":"ph"},
  "obsidian": {"vault_name":"myvault"},
  "telegram": {"owner_user_id":111,"bot_token":"TKN","commands":[]}, "hermes": {"model":"openai/gpt-5-codex"},
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
        # Per upstream livesync: obfuscatePassphrase must equal passphrase.
        assert couchdb_peer["obfuscatePassphrase"] == "ph"
        assert "name" in couchdb_peer and "name" in storage_peer
        assert storage_peer["baseDir"] == "/opt/data/vault/"
        assert couchdb_peer["group"] == storage_peer["group"]
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]

def test_render_soul_template_substitution():
    assert configure.render_soul_template(
        "I am <agent.name>, owner is <name>.", SAMPLE
    ) == "I am Jarvis, owner is Michael."

def test_render_soul_template_unknown_token_left_intact():
    # Unresolvable tokens stay as-is so the user can see what broke.
    assert configure.render_soul_template(
        "Hi <nonexistent.key>, hello <agent.name>.", SAMPLE
    ) == "Hi <nonexistent.key>, hello Jarvis."

def test_generate_hermes_soul_writes_rendered_soul(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        configure.generate_hermes_soul(SAMPLE)
        soul = (tmp_path / "hermes" / "SOUL.md").read_text()
        assert "I am Jarvis" in soul
        assert "Michael's hub" in soul
        assert "Vault: myvault" in soul
        assert "<" not in soul  # no unrendered tokens
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]

def test_generate_agents_md_platform_only(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        sample = {**SAMPLE, "agent": {**SAMPLE["agent"], "instructions": None}}
        configure.generate_agents_md(sample)
        body = (tmp_path / "AGENTS.md").read_text()
        # Tokens pre-substituted at render time.
        assert "<name>" not in body and "<obsidian.vault_name>" not in body
        # Concrete platform facts present.
        assert "Michael's self-hosted personal hub" in body
        assert "/opt/data/vault/" in body
        assert "obsidian://open?vault=myvault" in body
        assert "everstone-tasks" in body
        # No more MCP reference — CLI is the whole interface for tasks.
        assert "everstone_tasks" not in body
        # No operator section if instructions is null.
        assert "## Custom instructions" not in body
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]

def test_generate_agents_md_appends_instructions(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        sample = {**SAMPLE, "agent": {**SAMPLE["agent"],
            "instructions": "Tasks from <name>'s family chat go to the Family list."}}
        configure.generate_agents_md(sample)
        body = (tmp_path / "AGENTS.md").read_text()
        # Tokens substituted inside instructions too (same render pass).
        assert "Michael's family chat" in body
        # Order: platform first, then custom instructions header.
        platform_idx = body.index("## EverStone platform")
        custom_idx = body.index("## Custom instructions")
        assert platform_idx < custom_idx
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]

def test_generate_hermes_env_sets_terminal_cwd(tmp_path):
    os.environ["EVERSTONE_CONFIG_DIR"] = str(tmp_path)
    try:
        configure.generate_hermes_env(SAMPLE)
        envdir = tmp_path / "hermes" / "envdir"
        assert (envdir / "HERMES_TERMINAL_CWD").read_text() == "/opt/data"
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]

def test_generate_hermes_env_telegram_commands_default_empty(tmp_path):
    os.environ["EVERSTONE_CONFIG_DIR"] = str(tmp_path)
    try:
        sample = {**SAMPLE, "telegram": {**SAMPLE["telegram"], "commands": []}}
        configure.generate_hermes_env(sample)
        envdir = tmp_path / "hermes" / "envdir"
        assert (envdir / "TELEGRAM_COMMANDS").read_text() == "[]"
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]

def test_generate_hermes_env_telegram_commands_translated(tmp_path):
    os.environ["EVERSTONE_CONFIG_DIR"] = str(tmp_path)
    try:
        sample = {**SAMPLE, "telegram": {**SAMPLE["telegram"], "commands": [
            {"cmd": "tasks", "desc": "Show open tasks"},
            {"cmd": "today", "desc": "What's on for today"},
        ]}}
        configure.generate_hermes_env(sample)
        body = (tmp_path / "hermes" / "envdir" / "TELEGRAM_COMMANDS").read_text()
        # Our cmd/desc → Bot API command/description
        assert '"command": "tasks"' in body
        assert '"description": "Show open tasks"' in body
        assert '"command": "today"' in body
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]

def test_generate_hermes_soul_overwrites_each_time(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        (tmp_path / "hermes").mkdir()
        (tmp_path / "hermes" / "SOUL.md").write_text("stale custom content")
        configure.generate_hermes_soul(SAMPLE)
        # always-overwrite: stale content gone, new render in place
        assert "stale custom content" not in (tmp_path / "hermes" / "SOUL.md").read_text()
        assert "I am Jarvis" in (tmp_path / "hermes" / "SOUL.md").read_text()
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]

def test_generate_setup_livesync_script(tmp_path):
    out = tmp_path / "setup-obsidian-livesync"
    os.environ["EVERSTONE_SETUP_LIVESYNC_PATH"] = str(out)
    saved_defaults = configure.DEFAULTS_CONFIG_DIR
    configure.DEFAULTS_CONFIG_DIR = ROOT / "config"
    try:
        configure.generate_setup_livesync_script(SAMPLE)
        body = out.read_text()
        # all substitutions made (no template tokens left behind)
        for tok in ("{{COUCHDB_USER}}", "{{COUCHDB_PASSWORD}}", "{{COUCHDB_DATABASE}}",
                    "{{LIVESYNC_PASSPHRASE}}", "{{PUBLIC_URL}}"):
            assert tok not in body, f"template token {tok} not substituted"
        # injected values present
        assert "export COUCHDB_URI='https://example.com/db'" in body
        assert "export LIVESYNC_PASSPHRASE='ph'" in body
        assert "export DB_USER='u'" in body
        # executable
        assert out.stat().st_mode & 0o100
    finally:
        del os.environ["EVERSTONE_SETUP_LIVESYNC_PATH"]
        configure.DEFAULTS_CONFIG_DIR = saved_defaults


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
        assert (envdir / "EVERSTONE_GROUP_BINARIES").read_text() == "everstone-tasks"
        # sourceable env file for setup_hermes
        env_file = (tmp_path / "hermes" / "env").read_text()
        assert "export TELEGRAM_ALLOWED_USERS=111" in env_file
        assert "export HERMES_MODEL=openai/gpt-5-codex" in env_file
    finally:
        del os.environ["EVERSTONE_CONFIG_DIR"]
