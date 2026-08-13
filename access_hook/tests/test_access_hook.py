import os
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "everstone_access_hook",
    Path(__file__).resolve().parents[1] / "everstone_access_hook.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Expose top-level names for tests that call them directly.
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


# --- DM: trust the operator ---
def test_dm_allows_any_tool():
    for tool in ("terminal", "read_file", "es_notes_journal", "web_search",
                 "es_tasks_add", "es_cal_delete"):
        assert run_policy(tool, "agent:main:telegram:dm:111") is None, tool


# --- Group: only es_tasks_*, es_cal_* and es_weather allowed ---
def test_group_allows_es_weather():
    # Weather is group-safe: it reveals nothing private and is useful in shared
    # chats. NB the prefix has no trailing underscore — it's a single es_weather.
    assert run_policy("es_weather", "agent:main:telegram:group:-100") is None


def test_group_allows_es_tasks_tools():
    for tool in ("es_tasks_list", "es_tasks_add", "es_tasks_clear"):
        assert run_policy(tool, "agent:main:telegram:group:-100") is None, tool


def test_group_allows_es_cal_tools():
    for tool in ("es_cal_agenda", "es_cal_add", "es_cal_delete"):
        assert run_policy(tool, "agent:main:telegram:group:-100") is None, tool


def test_group_blocks_es_notes_tools():
    for tool in ("es_notes_journal", "es_notes_read", "es_notes_topics"):
        r = run_policy(tool, "agent:main:telegram:group:-100")
        assert r is not None and "block" in str(r), tool


def test_group_blocks_non_es_tools():
    for tool in ("terminal", "read_file", "write_file", "web_search", "browser"):
        r = run_policy(tool, "agent:main:telegram:group:-100")
        assert r is not None and "block" in str(r), tool


def test_group_blocks_es_contacts_tools():
    # Contacts are private: es_contacts_* is neither es_tasks_* nor es_cal_*,
    # so it's blocked in groups (DM-only).
    r = run_policy("es_contacts_search", "agent:main:telegram:group:-100")
    assert r is not None and "block" in str(r)


def test_supergroup_treated_same():
    assert run_policy("es_tasks_list", "agent:main:telegram:supergroup:-100") is None
    assert run_policy("es_notes_read", "agent:main:telegram:supergroup:-100") is not None


# --- Fail-closed fallbacks ---
def test_no_session_blocks():
    assert run_policy("es_tasks_list", None) is not None
    assert run_policy("read_file", None) is not None


def test_hook_never_raises(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", "agent:main:telegram:group:123")
    # tool_input shape is irrelevant now (no command parsing); must not raise.
    assert mod.HermesPlugin().pre_tool_call("es_notes_journal", tool_input=12345)["action"] == "block"
    assert mod.HermesPlugin().pre_tool_call("es_tasks_add", tool_input=12345) is None
