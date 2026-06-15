import json
import subprocess


# Hermes, the access-hook plugin, and es live in the agent venv (canonical
# checkout+venv layout) — NOT system python. Check the hook through the venv.
_VENV_PY = "/usr/local/lib/hermes-agent/.venv/bin/python"


_PY_SNIPPET = (
    "import json, os, everstone_access_hook as h, sys;"
    "tool = os.environ['E2E_TOOL'];"
    "cmd = os.environ.get('E2E_COMMAND');"
    "tool_input = {'command': cmd} if cmd else None;"
    "sys.stdout.write(json.dumps(h.policy(tool, tool_input)))"
)


def _policy(container, tool_name, session_key=None, command=None):
    """Run the access hook's policy() inside the container with controlled env."""
    env = []
    if session_key is not None:
        env += ["-e", f"HERMES_SESSION_KEY={session_key}"]
    if command is not None:
        env += ["-e", f"E2E_COMMAND={command}"]
    env += ["-e", f"E2E_TOOL={tool_name}"]
    cmd = ["docker", "exec", *env, container, _VENV_PY, "-c", _PY_SNIPPET]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(r.stdout.strip())


def test_dm_allows_terminal(everstone):
    assert (
        _policy(
            everstone["container_name"],
            "terminal",
            session_key="agent:main:telegram:dm:111",
        )
        is None
    )


def test_dm_allows_engraph_and_others(everstone):
    for tool in ("terminal", "read_file", "engraph", "spawn_subagent", "everstone_tasks"):
        assert (
            _policy(
                everstone["container_name"],
                tool,
                session_key="agent:main:telegram:dm:111",
            )
            is None
        ), tool


def test_group_blocks_shell_and_notes(everstone):
    for tool in ("terminal", "read_file", "engraph", "spawn_subagent"):
        result = _policy(
            everstone["container_name"],
            tool,
            session_key="agent:main:telegram:group:-100",
        )
        assert result is not None and "block" in str(result), tool


def test_group_allows_tasks(everstone):
    # Group chats allow terminal with an `es tasks ...` command (no composition).
    assert (
        _policy(
            everstone["container_name"],
            "terminal",
            session_key="agent:main:telegram:group:-100",
            command="es tasks list --list inbox",
        )
        is None
    )
    # supergroup too
    assert (
        _policy(
            everstone["container_name"],
            "terminal",
            session_key="agent:main:telegram:supergroup:-100",
            command="es tasks list --list inbox",
        )
        is None
    )


def test_opaque_session_fails_closed(everstone):
    result = _policy(
        everstone["container_name"], "terminal", session_key="sess_opaque"
    )
    assert result is not None and "block" in str(result)


def test_no_session_fails_closed(everstone):
    result = _policy(everstone["container_name"], "terminal", session_key=None)
    assert result is not None and "block" in str(result)


def test_lockdown_config_in_image(everstone):
    """The image has the access hook plugin and Telegram allowlist env wired up."""
    # Verify the plugin entry point is registered
    r = subprocess.run(
        [
            "docker",
            "exec",
            everstone["container_name"],
            _VENV_PY,
            "-c",
            "from importlib.metadata import entry_points; "
            "eps = entry_points(group='hermes_plugins'); "
            "print([ep.name for ep in eps])",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "everstone_access_hook" in r.stdout, r.stdout

    # Verify TELEGRAM_ALLOWED_USERS is written to the Hermes profile config.yaml
    # by setup_hermes (s6 oneshot), which may finish slightly after /health is up.
    # Poll up to ~30 s to avoid a race.
    import time as _time
    deadline = _time.monotonic() + 30
    while True:
        r = subprocess.run(
            ["docker", "exec", everstone["container_name"],
             "grep", "-E", "^TELEGRAM_ALLOWED_USERS:",
             "/opt/data/hermes/profiles/everstone/config.yaml"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and "123456" in r.stdout:
            break
        if _time.monotonic() >= deadline:
            assert False, (
                f"TELEGRAM_ALLOWED_USERS not found in profile config.yaml after 30 s; "
                f"grep stdout={r.stdout!r} stderr={r.stderr!r}"
            )
        _time.sleep(1)

    # Verify no GATEWAY_ALLOW_ALL_USERS leaks into the profile config or .env
    r = subprocess.run(
        ["docker", "exec", everstone["container_name"], "sh", "-c",
         "cat /opt/data/hermes/profiles/everstone/config.yaml"
         " /opt/data/hermes/profiles/everstone/.env 2>/dev/null"],
        capture_output=True, text=True, check=True,
    )
    assert "GATEWAY_ALLOW_ALL_USERS" not in r.stdout
