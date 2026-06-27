"""The Brave Search API key is config-driven: config.yaml's `brave.api_key` is
wired by setup_hermes into the everstone profile's .env as BRAVE_SEARCH_API_KEY
(the env var Hermes' web_tools backend selector checks). The e2e config sets
brave.api_key: test-brave-key-e2e, so a booted container must carry it through.
"""
import subprocess


def _exec(container, *args):
    return subprocess.run(["docker", "exec", container, *args],
                          capture_output=True, text=True)


def test_brave_key_wired_into_profile_env(everstone):
    c = everstone["container_name"]
    env = _exec(c, "cat", "/opt/data/hermes/profiles/everstone/.env").stdout
    assert "BRAVE_SEARCH_API_KEY=test-brave-key-e2e" in env, (
        "config.brave.api_key was not wired into the profile .env as "
        f"BRAVE_SEARCH_API_KEY. .env contents:\n{env}"
    )
