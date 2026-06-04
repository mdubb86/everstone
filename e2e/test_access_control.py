import json
import subprocess


_PY_SNIPPET = (
    "import json, os, everstone_access_hook as h, sys;"
    "tool = os.environ['E2E_TOOL'];"
    "sys.stdout.write(json.dumps(h.policy(tool)))"
)


def _policy(container, tool_name, session_key=None, group_tools=None):
    """Run the access hook's policy() inside the container with controlled env."""
    env = []
    if session_key is not None:
        env += ["-e", f"HERMES_SESSION_KEY={session_key}"]
    if group_tools is not None:
        env += ["-e", f"EVERSTONE_GROUP_TOOLS={group_tools}"]
    env += ["-e", f"E2E_TOOL={tool_name}"]
    cmd = ["docker", "exec", *env, container, "python3", "-c", _PY_SNIPPET]
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
    assert (
        _policy(
            everstone["container_name"],
            "everstone_tasks",
            session_key="agent:main:telegram:group:-100",
        )
        is None
    )
    # supergroup too
    assert (
        _policy(
            everstone["container_name"],
            "everstone_tasks",
            session_key="agent:main:telegram:supergroup:-100",
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


def test_group_tools_env_widens_allowlist(everstone):
    # In group, with the env widened, additional tools are allowed
    assert (
        _policy(
            everstone["container_name"],
            "extra_tool",
            session_key="agent:main:telegram:group:-100",
            group_tools="everstone_tasks,extra_tool",
        )
        is None
    )


def test_lockdown_config_in_image(everstone):
    """The image has the access hook plugin and Telegram allowlist env wired up."""
    # Verify the plugin entry point is registered
    r = subprocess.run(
        [
            "docker",
            "exec",
            everstone["container_name"],
            "python3",
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

    # Verify TELEGRAM_ALLOWED_USERS env is generated and present
    r = subprocess.run(
        [
            "docker",
            "exec",
            everstone["container_name"],
            "cat",
            "/opt/config/hermes/envdir/TELEGRAM_ALLOWED_USERS",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert r.stdout.strip() == "123456"  # matches conftest sample config

    # Verify no GATEWAY_ALLOW_ALL_USERS leaks in
    r = subprocess.run(
        [
            "docker",
            "exec",
            everstone["container_name"],
            "ls",
            "/opt/config/hermes/envdir/",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "GATEWAY_ALLOW_ALL_USERS" not in r.stdout
