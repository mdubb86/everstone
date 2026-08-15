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


def test_maps_write_tools_are_registered(everstone):
    """The Saved Places tools drive the authenticated browser, so behaviour cannot be
    exercised in a throwaway container (no Google session). Registration and import
    wiring can be, and that is what breaks when capabilities are added."""
    r = _exec(everstone["container_name"], VENV_PY, "-c",
              "import asyncio; from es.mcp_server import mcp;"
              "n=[t.name for t in asyncio.run(mcp.list_tools())];"
              "print(','.join(sorted(x for x in n if x.startswith('es_maps_'))))")
    assert r.returncode == 0, r.stderr
    got = set(r.stdout.strip().split(","))
    for tool in ("es_maps_star", "es_maps_unstar", "es_maps_lists",
                 "es_maps_list_places", "es_maps_place_lists", "es_maps_resolve"):
        assert tool in got, f"{tool} not registered; got {sorted(got)}"


def test_maps_tools_require_place_id_not_free_text(everstone):
    """Free-text resolution was removed from star/unstar: it searched all of Google Maps
    rather than the operator's saved places, so an unstar could silently target a
    different branch and report changed=false."""
    r = _exec(everstone["container_name"], VENV_PY, "-c",
              "import asyncio,json; from es.mcp_server import mcp;"
              "t={x.name:x for x in asyncio.run(mcp.list_tools())};"
              "print(json.dumps({k:(t[k].inputSchema or {}).get('required') "
              "for k in ('es_maps_star','es_maps_unstar')}))")
    assert r.returncode == 0, r.stderr
    import json as _j
    req = _j.loads(r.stdout)
    assert req["es_maps_star"] == ["place_id"], req
    assert req["es_maps_unstar"] == ["place_id"], req
