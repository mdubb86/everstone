import os
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "everstone_access_hook",
    Path(__file__).resolve().parents[1] / "everstone_access_hook.py"
)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def run_policy(tool_name, session_key, env=None):
    original = os.environ.copy()
    try:
        if env:
            os.environ.update(env)
        if session_key is not None:
            os.environ["HERMES_SESSION_KEY"] = session_key
        elif "HERMES_SESSION_KEY" in os.environ:
            del os.environ["HERMES_SESSION_KEY"]
        return mod.policy(tool_name)
    finally:
        os.environ.clear(); os.environ.update(original)

def test_dm_allows_terminal():
    assert run_policy("terminal", "agent:main:telegram:dm:111") is None

def test_group_blocks_terminal():
    result = run_policy("terminal", "agent:main:telegram:group:-100")
    assert result is not None and "block" in str(result)

def test_group_allows_tasks():
    assert run_policy("everstone_tasks", "agent:main:telegram:group:-100") is None

def test_unparseable_denies_terminal():
    result = run_policy("terminal", "sess_opaque")
    assert result is not None and "block" in str(result)

def test_unparseable_allows_tasks():
    assert run_policy("everstone_tasks", "sess_opaque") is None

def test_no_session_key_denies_terminal():
    result = run_policy("terminal", None)
    assert result is not None and "block" in str(result)
