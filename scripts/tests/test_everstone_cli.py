import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("everstone_cli", ROOT / "scripts" / "everstone_cli.py")
cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)


def test_provider_from_model():
    assert cli._provider_from_model("openai-codex/gpt-5.5") == "openai-codex"
    assert cli._provider_from_model("anthropic/claude-opus-4") == "anthropic"


def test_provider_from_model_requires_slash():
    import pytest
    with pytest.raises(SystemExit):
        cli._provider_from_model("gpt-5.5")


def test_mcp_add_message_prints_claude_add_line():
    cfg = {"public_url": "https://everstone.waage.haus/",
           "mcp_http": {"enabled": True, "token": "tok-ABC_123"}}
    msg = cli._mcp_add_message(cfg)
    assert msg == (
        'claude mcp add --transport http everstone '
        'https://everstone.waage.haus/mcp '
        '--header "Authorization: Bearer tok-ABC_123"'
    )


def test_mcp_add_message_hint_when_disabled():
    msg = cli._mcp_add_message({"public_url": "https://e.test"})
    assert "disabled" in msg
    assert "claude mcp add" not in msg


def test_mcp_add_message_hint_when_token_missing():
    cfg = {"public_url": "https://e.test", "mcp_http": {"enabled": True}}
    msg = cli._mcp_add_message(cfg)
    assert "token is empty" in msg
    assert "claude mcp add" not in msg
