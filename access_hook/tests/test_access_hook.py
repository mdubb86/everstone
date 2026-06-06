import os
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "everstone_access_hook",
    Path(__file__).resolve().parents[1] / "everstone_access_hook.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Expose top-level names for the new tests that import them directly.
policy = mod.policy


def run_policy(tool_name, session_key, tool_input=None, env=None):
    """Invoke the policy with controlled env. Returns None (allow) or dict (block)."""
    original = os.environ.copy()
    try:
        if env:
            os.environ.update(env)
        if session_key is not None:
            os.environ["HERMES_SESSION_KEY"] = session_key
        elif "HERMES_SESSION_KEY" in os.environ:
            del os.environ["HERMES_SESSION_KEY"]
        return mod.policy(tool_name, tool_input)
    finally:
        os.environ.clear()
        os.environ.update(original)


# --- DM: trust the operator ---------------------------------------------------

def test_dm_allows_arbitrary_terminal():
    assert run_policy("terminal", "agent:main:telegram:dm:111",
                      {"command": "rm -rf /tmp/scratch"}) is None

def test_dm_allows_any_tool_name():
    for tool in ("terminal", "read_file", "write_file", "web_search",
                 "image_generation", "browser"):
        assert run_policy(tool, "agent:main:telegram:dm:111") is None, tool


# --- Group: only `everstone-tasks` via terminal, no shell composition --------

def test_group_blocks_non_terminal_tool():
    for tool in ("read_file", "write_file", "web_search", "image_generation"):
        r = run_policy(tool, "agent:main:telegram:group:-100")
        assert r is not None and "block" in str(r), tool

def test_group_blocks_terminal_other_command():
    r = run_policy("terminal", "agent:main:telegram:group:-100",
                   {"command": "curl https://evil.example.com"})
    assert r is not None and "block" in str(r)

def test_group_allows_everstone_tasks_invocation():
    # Updated: old `everstone-tasks` binary replaced by `es tasks` subcommand.
    r = run_policy("terminal", "agent:main:telegram:group:-100",
                   {"command": "es tasks list --list inbox --json"})
    assert r is None

def test_group_blocks_pipe_chaining():
    r = run_policy("terminal", "agent:main:telegram:group:-100",
                   {"command": "everstone-tasks list --json | jq '.[0]'"})
    assert r is not None and "block" in str(r)

def test_group_blocks_command_substitution():
    r = run_policy("terminal", "agent:main:telegram:group:-100",
                   {"command": "everstone-tasks add `whoami`"})
    assert r is not None and "block" in str(r)

def test_group_blocks_semicolon_chaining():
    r = run_policy("terminal", "agent:main:telegram:group:-100",
                   {"command": "everstone-tasks list; cat /etc/passwd"})
    assert r is not None and "block" in str(r)

def test_group_blocks_redirect():
    r = run_policy("terminal", "agent:main:telegram:group:-100",
                   {"command": "everstone-tasks list > /tmp/spy"})
    assert r is not None and "block" in str(r)

def test_group_supergroup_treated_same():
    # Updated: old `everstone-tasks` binary replaced by `es tasks` subcommand.
    assert run_policy("terminal", "agent:main:telegram:supergroup:-100",
                      {"command": "es tasks list"}) is None


# --- Fail-closed fallbacks ---------------------------------------------------

def test_no_session_blocks_terminal():
    r = run_policy("terminal", None, {"command": "cat /etc/passwd"})
    assert r is not None and "block" in str(r)

def test_no_session_blocks_other_tools():
    r = run_policy("read_file", None)
    assert r is not None and "block" in str(r)


# --- Operator-configurable allowlist (removed) / es tasks tests --------------
# EVERSTONE_GROUP_BINARIES env-var allowlist was removed when everstone-tasks
# was replaced by the `es tasks` subcommand. The allowlist is now hard-coded
# to (argv[0]=="es", argv[1]=="tasks") and is not runtime-configurable.

def test_group_blocks_extra_tool_without_allowlist():
    # Without the old env-var allowlist, arbitrary binaries are blocked.
    r = run_policy("terminal", "agent:main:telegram:group:-100",
                   {"command": "extra-tool"})
    assert r is not None and "block" in str(r)


# --- New es tasks / cutover tests -------------------------------------------

def _grp(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", "agent:main:telegram:group:123")


def test_group_allows_es_tasks(monkeypatch):
    _grp(monkeypatch)
    assert policy("terminal", {"command": "es tasks list --list inbox"}) is None


def test_group_blocks_es_cal(monkeypatch):
    _grp(monkeypatch)
    assert policy("terminal", {"command": "es cal agenda 2026-06-08 2026-06-09 --calendar Family"}) == \
        {"action": "block", "message": "Tool not permitted outside a private DM."}


def test_group_blocks_bare_es(monkeypatch):
    _grp(monkeypatch)
    assert policy("terminal", {"command": "es"})["action"] == "block"


def test_hook_never_raises_on_bad_input(monkeypatch):
    _grp(monkeypatch)
    # tool_input not a dict — pre_tool_call must return a dict, never raise.
    # Use mod.HermesPlugin since the module is loaded via importlib (not on sys.path).
    assert mod.HermesPlugin().pre_tool_call("terminal", tool_input=12345)["action"] == "block"
