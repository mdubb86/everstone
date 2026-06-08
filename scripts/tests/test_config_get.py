import os
import subprocess
import sys
import pathlib

SCRIPT = str(pathlib.Path(__file__).parents[1] / "config-get")


def _run(key, cfg_path):
    return subprocess.run(
        [sys.executable, SCRIPT, key],
        capture_output=True,
        text=True,
        env={**os.environ, "ES_CONFIG_PATH": str(cfg_path)},
    )


def test_scalar(tmp_path):
    c = tmp_path / "config.yaml"
    c.write_text("telegram: {bot_token: TKN}\n")
    r = _run("telegram.bot_token", c)
    assert r.returncode == 0 and r.stdout.strip() == "TKN"


def test_missing_is_empty(tmp_path):
    c = tmp_path / "config.yaml"
    c.write_text("telegram: {bot_token: TKN}\n")
    r = _run("github.token", c)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_list_space_joined(tmp_path):
    c = tmp_path / "config.yaml"
    c.write_text("agent: {skills: [a, b, c]}\n")
    r = _run("agent.skills", c)
    assert r.returncode == 0 and r.stdout.strip() == "a b c"
