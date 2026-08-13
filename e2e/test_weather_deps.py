"""Guards the two es dependencies that are only exercised inside the image.

`timezonefinder` ships tens of MB of polygon data as package data, and `es` is
installed into the Hermes venv via `uv pip install /opt/es` — so a dependency
that resolves fine on a dev machine can still land without its data in the
image. Unit tests cannot see this; only a real container can.
"""
import json
import subprocess

VENV_PY = "/usr/local/lib/hermes-agent/.venv/bin/python"


def _exec(container, *args):
    return subprocess.run(["docker", "exec", container, *args],
                          capture_output=True, text=True)


def test_timezonefinder_resolves_a_zone_inside_the_image(everstone):
    """Import AND a real lookup: importing proves the package installed, the
    lookup proves its polygon data came with it."""
    r = _exec(everstone["container_name"], VENV_PY, "-c",
              "from timezonefinder import TimezoneFinder;"
              "print(TimezoneFinder().timezone_at(lat=37.7749, lng=-122.4194))")
    assert r.returncode == 0, f"timezonefinder unusable in the image: {r.stderr}"
    assert r.stdout.strip() == "America/Los_Angeles", r.stdout


def test_es_weather_tool_is_registered(everstone):
    """es_weather must be exposed by the MCP server the agent actually loads."""
    r = _exec(everstone["container_name"], VENV_PY, "-c",
              "from es import mcp_server; print(hasattr(mcp_server, 'es_weather'))")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "True", r.stdout


def test_weather_units_default_is_readable(everstone):
    """weather.units must resolve even when config.yaml omits the block —
    otherwise every weather call fails on a config that never mentioned it."""
    r = _exec(everstone["container_name"], VENV_PY, "-c",
              "from es.capabilities import weather; print(weather.units())")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() in ("imperial", "metric"), r.stdout
