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
