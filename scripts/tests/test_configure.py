import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("configure", ROOT/"scripts"/"configure.py")
configure = importlib.util.module_from_spec(spec); spec.loader.exec_module(configure)

SAMPLE = {
  "public_url": "https://example.com",
  "name": "Michael",
  "agent": {"name": "Jarvis", "soul": "I am <agent.name>, in <name>'s hub. Vault: <obsidian.vault_name>.", "skills": []},
  "couchdb": {"user":"u","password":"p","database":"vault"},
  "caldav": {"user":"cu","password":"cp"},
  "livesync": {"passphrase":"ph", "tweaks": {"customChunkSize":60, "chunkSplitterVersion":"v3-rabin-karp", "hashAlg":"xxhash64", "doNotUseFixedRevisionForChunks":True, "handleFilenameCaseSensitive":False}},
  "obsidian": {"vault_name":"myvault"},
  "telegram": {"owner_user_id":111,"bot_token":"TKN","commands":[]},
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
        # Reliable sync of es-notes writes: reconcile the vault on every (re)start,
        # and use chokidar so writes into freshly-created subdirs aren't dropped.
        assert storage_peer["scanOfflineChanges"] is True
        assert storage_peer["useChokidar"] is True
        # Bridge aligns to the plugins' chunk/E2EE format at runtime via
        # useRemoteTweaks, and seeds the same canonical values (defaults.yaml
        # livesync.tweaks) for cold start — single source of truth, no drift.
        assert couchdb_peer["useRemoteTweaks"] is True
        assert couchdb_peer["customChunkSize"] == 60
        assert couchdb_peer["chunkSplitterVersion"] == "v3-rabin-karp"
        assert couchdb_peer["doNotUseFixedRevisionForChunks"] is True
        assert couchdb_peer["handleFilenameCaseSensitive"] is False
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
        soul = (tmp_path / "hermes" / "profiles" / "everstone" / "SOUL.md").read_text()
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
        assert "myvault" in body                       # vault name substituted
        # MCP-tool reality: the agent acts through es_* tools, not a shell/CLI.
        assert "es_tasks_" in body
        assert "es_notes_" in body
        assert "es_contacts_search" in body
        assert "es tasks" not in body                  # no CLI invocation
        assert "there is no MCP" not in body           # stale claim gone
        assert "read_file" not in body                 # no file-tool guidance
        assert "everstone_tasks" not in body
        # Web research: search-first, escalate to the browser.
        assert "web_search" in body
        assert "browser" in body
        assert "es_web_fetch" in body
        # No-fabrication guardrail present.
        assert "fabricate" in body and "unverified" in body
        # No operator section if instructions is null.
        assert "## Custom instructions" not in body
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]

def test_generate_agents_md_documents_guidance(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        sample = {**SAMPLE, "agent": {**SAMPLE["agent"], "instructions": None}}
        configure.generate_agents_md(sample)
        body = (tmp_path / "AGENTS.md").read_text()
        # The tools are named, and the agent is told where the path comes from.
        assert "es_doc_extract" in body
        assert "es_doc_render" in body
        assert "It is saved at:" in body
        assert "vision_analyze" in body
        # The crux: an explicit override of Hermes's injected attachment note,
        # which points the (toolless) agent at a terminal/skill it doesn't have.
        assert "no terminal tool" in body
        assert "ocr-and-documents" in body
        # The receipt contract, pinned so it can't silently drift back to the
        # old (false) "returns it as Markdown" claim: extract hands back a
        # doc_id + preview + complete, and the rest is read via es_read.
        assert "returns it as" not in body
        assert "receipt" in body
        assert 'es_read(target="doc:<doc_id>")' in body
        assert "complete: true" in body
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

def test_generate_agents_md_calendar_section_absent_when_unconfigured(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        configure.generate_agents_md(SAMPLE)
        body = (tmp_path / "AGENTS.md").read_text()
        # The es_cal_* tools are always registered, so the tool overview may
        # mention calendar; what must be absent is the detailed policy SECTION.
        assert "### Calendar" not in body
        assert "READ-ONLY" not in body and "READ-WRITE" not in body
        assert "gcal" not in body
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]


def test_generate_agents_md_calendar_section_absent_when_lists_empty(tmp_path):
    """gcalcli configured but with no calendars yet — agent gets no Calendar section.

    This is the "I've pasted my OAuth creds but haven't discovered my calendar
    IDs yet" intermediate state. Auth works (`everstone auth gcal`, `gcal list`)
    but the agent doesn't see a Calendar section in AGENTS.md until the
    operator fills in at least one list.
    """
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        sample = {**SAMPLE, "gcalcli": {
            "client_id": "cid",
            "client_secret": "csec",
            "calendars": {"read_only": [], "read_write": []},
        }}
        configure.generate_agents_md(sample)
        body = (tmp_path / "AGENTS.md").read_text()
        # No detailed Calendar policy section until a list is filled in.
        assert "### Calendar" not in body
        assert "READ-ONLY" not in body and "READ-WRITE" not in body
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]


def test_generate_agents_md_calendar_section_renders_lists(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        sample = {**SAMPLE, "gcalcli": {
            "client_id": "cid",
            "client_secret": "csec",
            "calendars": {
                "read_only": ["primary", "family@example.com"],
                "read_write": ["personal@gmail.com"],
            },
        }}
        configure.generate_agents_md(sample)
        body = (tmp_path / "AGENTS.md").read_text()
        assert "### Calendar — Google Calendar via the `es_cal_*` tools" in body
        # Calendars must appear in correct section, with the exact name the
        # operator typed (no munging, so primary / email-form both work).
        ro_idx = body.index("READ-ONLY")
        rw_idx = body.index("READ-WRITE")
        assert body.index("`primary`") < rw_idx
        assert body.index("`family@example.com`") < rw_idx
        assert body.index("`personal@gmail.com`") > rw_idx
    finally:
        del os.environ["EVERSTONE_DATA_DIR"]


def test_telegram_commands_payload():
    cfg = {"telegram": {"commands": [{"cmd": "ping", "desc": "check"}]}}
    assert configure._telegram_commands(cfg) == [{"command": "ping", "description": "check"}]

def test_telegram_commands_empty_default():
    assert configure._telegram_commands({"telegram": {}}) == []

def test_generate_hermes_soul_overwrites_each_time(tmp_path):
    os.environ["EVERSTONE_DATA_DIR"] = str(tmp_path)
    try:
        soul_path = tmp_path / "hermes" / "profiles" / "everstone" / "SOUL.md"
        soul_path.parent.mkdir(parents=True, exist_ok=True)
        soul_path.write_text("stale custom content")
        configure.generate_hermes_soul(SAMPLE)
        # always-overwrite: stale content gone, new render in place
        assert "stale custom content" not in soul_path.read_text()
        assert "I am Jarvis" in soul_path.read_text()
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


def test_config_schema_has_no_hermes_section():
    import json, pathlib
    schema = json.loads((pathlib.Path(__file__).parents[2] / "config/schema.json").read_text())
    assert "hermes" not in schema.get("properties", {}), "hermes.model must be removed from schema"
    assert "hermes" not in schema.get("required", []), "hermes must be removed from required"


def test_config_schema_has_brave_api_key():
    import json, pathlib
    schema = json.loads((pathlib.Path(__file__).parents[2] / "config/schema.json").read_text())
    brave = schema["properties"].get("brave")
    assert brave is not None, "brave section missing from schema"
    assert "api_key" in brave["properties"], "brave.api_key missing from schema"
    # Optional, like github — never required.
    assert "brave" not in schema.get("required", [])


def test_migrate_vault_folders_renames_legacy(tmp_path):
    vault = tmp_path / "vault"
    (vault / "journal" / "2026-06-01").mkdir(parents=True)
    (vault / "topics").mkdir(parents=True)
    (vault / "topics" / "Home network.md").write_text("x")
    configure.migrate_vault_folders(vault, "Journal", ["Topics"])
    # On case-sensitive FS the dirs are renamed; on case-insensitive they already
    # ARE the target. Either way the capitalized names must resolve with content.
    assert (vault / "Journal" / "2026-06-01").is_dir()
    assert (vault / "Topics" / "Home network.md").is_file()


def test_migrate_vault_folders_noop_when_target_exists(tmp_path):
    vault = tmp_path / "vault"
    (vault / "journal").mkdir(parents=True)
    (vault / "journal" / "old.md").write_text("old")
    # exist_ok=True: on case-sensitive FS this creates a real second dir; on
    # case-insensitive FS (macOS dev) it's a no-op since "Journal" IS "journal".
    (vault / "Journal").mkdir(parents=True, exist_ok=True)
    (vault / "Journal" / "new.md").write_text("new")
    configure.migrate_vault_folders(vault, "Journal", ["Topics"])
    assert (vault / "Journal" / "new.md").read_text() == "new"  # not clobbered


def test_migrate_warns_and_leaves_when_both_distinct_folders_exist(tmp_path, capsys):
    # Custom journal_folder ('Diary') so legacy 'journal' and target 'Diary' are
    # genuinely distinct on any filesystem — the real "orphaned legacy" case.
    vault = tmp_path / "vault"
    (vault / "journal").mkdir(parents=True)
    (vault / "journal" / "legacy.md").write_text("legacy")
    (vault / "Diary").mkdir(parents=True)
    (vault / "Diary" / "current.md").write_text("current")
    configure.migrate_vault_folders(vault, "Diary", ["Topics"])
    out = capsys.readouterr().out
    assert "WARNING" in out and "journal" in out
    assert (vault / "journal" / "legacy.md").read_text() == "legacy"   # left in place
    assert (vault / "Diary" / "current.md").read_text() == "current"   # untouched


def test_migrate_idempotent_on_second_run(tmp_path):
    vault = tmp_path / "vault"
    (vault / "journal" / "2026-06-01").mkdir(parents=True)
    (vault / "journal" / "2026-06-01" / "e.md").write_text("entry")
    configure.migrate_vault_folders(vault, "Journal", ["Topics"])
    configure.migrate_vault_folders(vault, "Journal", ["Topics"])  # second run: stable
    assert (vault / "Journal" / "2026-06-01" / "e.md").read_text() == "entry"


def test_migrate_custom_journal_folder_and_category(tmp_path):
    vault = tmp_path / "vault"
    (vault / "journal" / "d").mkdir(parents=True)
    (vault / "topics").mkdir(parents=True)
    (vault / "topics" / "T.md").write_text("t")
    # journal -> Diary; topics -> first category (Projects) since 'Topics' absent
    configure.migrate_vault_folders(vault, "Diary", ["Projects", "People"])
    assert (vault / "Diary" / "d").is_dir()
    assert (vault / "Projects" / "T.md").read_text() == "t"


