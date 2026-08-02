import pytest
from es import config


def test_vault_root_default(monkeypatch):
    monkeypatch.delenv("ES_VAULT_PATH", raising=False)
    assert str(config.vault_root()) == "/opt/data/vault"


def test_vault_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ES_VAULT_PATH", str(tmp_path))
    assert config.vault_root() == tmp_path


def test_load_reads_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("caldav:\n  user: alice\n  password: secret\nobsidian:\n  vault_name: Vault\n")
    monkeypatch.setenv("ES_CONFIG_PATH", str(cfg))
    data = config.load_config()
    assert data["caldav"]["user"] == "alice"
    assert data["obsidian"]["vault_name"] == "Vault"


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ES_CONFIG_PATH", str(tmp_path / "nope.yaml"))
    with pytest.raises(FileNotFoundError):
        config.load_config()


def test_caldav_url_is_the_radicale_constant():
    assert config.CALDAV_URL == "http://localhost:5232"


def test_attach_source_dirs_defaults_to_hermes_cache():
    assert config.attach_source_dirs({}) == ["/opt/data/hermes/profiles/everstone/cache"]


def test_attach_source_dirs_honors_config_override():
    obs = {"attachments": {"sources": ["/a/cache", "/b/inbox"]}}
    assert config.attach_source_dirs(obs) == ["/a/cache", "/b/inbox"]


def test_attach_source_dirs_ignores_empty_override():
    assert config.attach_source_dirs({"attachments": {"sources": []}}) == \
        ["/opt/data/hermes/profiles/everstone/cache"]


def test_maps_config_defaults_empty():
    assert config.maps_config({}) == {}
    assert config.maps_config({"maps": {"api_key": "AIza"}}) == {"api_key": "AIza"}
