import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "merge_hermes_mcp", ROOT / "scripts" / "merge_hermes_mcp.py")
merge_hermes_mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge_hermes_mcp)

EXPECTED = {
    "command": "/usr/local/lib/hermes-agent/.venv/bin/es-mcp",
    "args": [],
    "env": {},
}


def test_registers_everstone_es_top_level():
    cfg = {}
    merge_hermes_mcp.apply(cfg)
    assert cfg["mcp_servers"]["everstone-es"] == EXPECTED


def test_scrubs_legacy_nested_keys():
    cfg = {"mcp": {"servers": {"everstone_tasks": {"command": "x"},
                               "engraph": {"command": "y"}}}}
    merge_hermes_mcp.apply(cfg)
    # legacy nested block is gone entirely (only es is registered, top-level)
    assert "mcp" not in cfg
    assert cfg["mcp_servers"]["everstone-es"] == EXPECTED


def test_scrubs_legacy_top_level_keys():
    cfg = {"mcp_servers": {"engraph": {"command": "y"}}}
    merge_hermes_mcp.apply(cfg)
    assert "engraph" not in cfg["mcp_servers"]
    assert cfg["mcp_servers"]["everstone-es"] == EXPECTED


def test_preserves_other_mcp_servers():
    cfg = {"mcp_servers": {"github": {"command": "gh-mcp", "args": []}}}
    merge_hermes_mcp.apply(cfg)
    assert cfg["mcp_servers"]["github"] == {"command": "gh-mcp", "args": []}
    assert cfg["mcp_servers"]["everstone-es"] == EXPECTED


def test_idempotent():
    cfg = {}
    merge_hermes_mcp.apply(cfg)
    once = {k: dict(v) for k, v in cfg["mcp_servers"].items()}
    merge_hermes_mcp.apply(cfg)
    assert cfg["mcp_servers"] == once
