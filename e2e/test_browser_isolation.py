"""Browser session isolation: camofox-flex (:9377) must never reach camofox-auth (:9378).

The branch's central security claim is structural, not behavioral: the flexible
`browser_*` toolset the agent drives is a DIFFERENT Camoufox process, on a different
port, with a different install root, a different config, and a different profile
directory from the one holding the authenticated Google session. Nothing but that
separation stops a flexible browse from touching (or endangering) the login.

These are regression guards, not one-time checks — a Dockerfile or run-script edit
could silently re-merge the two instances (e.g. by pointing flex at the shared install
root, or by handing it /opt/data profiles) and everything would still look healthy.
"""

import json
import time
import subprocess


FLEX = "http://localhost:9377"
AUTH = "http://localhost:9378"


def _exec(container, *args):
    return subprocess.run(["docker", "exec", container, *args],
                          capture_output=True, text=True)


def _curl(container, url):
    """curl from INSIDE the container — both instances bind container-localhost only."""
    return _exec(container, "curl", "-s", "-m", "10", url).stdout.strip()


def _health(container, url):
    raw = _curl(container, f"{url}/health")
    assert raw, f"no /health response from {url}"
    return json.loads(raw)


def _wait_browser(container, url, timeout_s=180):
    """Poll until Camoufox has actually launched behind the REST server.

    /health returns ok:true as soon as the Node server is listening — the browser
    pre-warm happens asynchronously after that, so the container being healthy does
    NOT mean the browser is up. Poll rather than sleep: pre-warm is ~1-4s warm but
    much slower on a cold first launch.
    """
    last = None
    for _ in range(timeout_s // 2):
        try:
            last = _health(container, url)
            if last.get("browserRunning") and last.get("browserConnected"):
                return last
        except Exception as e:  # server still coming up
            last = e
        time.sleep(2)
    raise AssertionError(f"{url} browser never came up; last /health: {last}")


def _svc_pid(container, service):
    """PID of an s6-supervised longrun, from `s6-svstat` output: 'up (pid N pgid N) ...'."""
    out = _exec(container, "s6-svstat", f"/run/service/{service}").stdout
    assert out.startswith("up"), f"{service} not up: {out}"
    return out.split("pid ", 1)[1].split()[0].rstrip(")")


def _node_json(container, expr):
    """Evaluate a node expression in the container and parse its JSON stdout."""
    r = _exec(container, "node", "-e", f'console.log(JSON.stringify({expr}))')
    assert r.returncode == 0, f"node failed: {r.stderr}"
    return json.loads(r.stdout)


# --- both instances exist, separately -----------------------------------------------

def test_both_instances_healthy_and_distinct(everstone):
    c = everstone["container_name"]
    for name, url in (("flex", FLEX), ("auth", AUTH)):
        h = _wait_browser(c, url)
        assert h["ok"] is True, f"{name} not ok: {h}"
    # Distinct processes running from DIFFERENT install roots — not one server
    # answering on two ports, and not two servers sharing a root (which would mean a
    # shared camofox.config.json and therefore a shared plugin set). The run scripts
    # `exec node server.js`, so the script name is gone from ps; ask s6 for the pid
    # and read its cwd, which is the install root the config is resolved from.
    roots = {}
    for svc in ("camofox-flex", "camofox-auth"):
        pid = _svc_pid(c, svc)
        roots[svc] = _exec(c, "readlink", f"/proc/{pid}/cwd").stdout.strip()
    assert roots["camofox-flex"] == "/opt/camofox-flex", roots
    assert roots["camofox-auth"] == "/opt/camofox-browser", roots


def test_both_services_supervised(everstone):
    c = everstone["container_name"]
    for svc in ("camofox-flex", "camofox-auth"):
        r = _exec(c, "s6-svstat", f"/run/service/{svc}")
        assert r.returncode == 0 and r.stdout.startswith("up"), f"{svc}: {r.stdout}{r.stderr}"
    # The old single service must be gone, not merely stopped.
    assert not _exec(c, "test", "-d", "/run/service/camofox-browser").returncode == 0


# --- the isolation itself -----------------------------------------------------------

def test_flex_has_no_plugins_auth_has_them(everstone):
    """Separate install roots are what make the plugin sets differ.

    camofox reads camofox.config.json from its own install ROOT_DIR (lib/config.js:
    ROOT_DIR = join(__dirname,'..')) with no env override, so a shared root would
    force both instances onto the same plugins.
    """
    c = everstone["container_name"]
    flex_plugins = _node_json(c, 'require("/opt/camofox-flex/camofox.config.json").plugins || {}')
    auth_plugins = _node_json(c, 'require("/opt/camofox-browser/camofox.config.json").plugins || {}')

    assert flex_plugins == {}, f"flex must run plugin-free, got {flex_plugins}"
    # fingerprint = the pinned identity that keeps the login from being re-challenged;
    # vnc = the interactive seeding surface. Both belong to auth alone.
    for p in ("fingerprint", "vnc"):
        assert auth_plugins.get(p, {}).get("enabled") is True, f"auth missing {p}: {auth_plugins}"


def test_flex_cannot_see_the_pinned_fingerprint(everstone):
    """A shared fingerprint would let flex traffic be correlated with the logged-in session."""
    c = everstone["container_name"]
    assert _exec(c, "test", "-f", "/opt/data/browser/fingerprint.json").returncode == 0, \
        "auth's pinned fingerprint should exist"
    # flex's root must not carry a copy, and its config must not point at one.
    assert _exec(c, "test", "-e", "/opt/camofox-flex/fingerprint.json").returncode != 0
    flex_cfg = _node_json(c, 'require("/opt/camofox-flex/camofox.config.json")')
    assert "fingerprint" not in json.dumps(flex_cfg).lower() or flex_cfg.get("plugins") == {}

    # Defense in depth: flex must not even HAVE the pinning plugin on disk. This holds only
    # because the Dockerfile copies the flex root BEFORE `COPY camofox-plugins/fingerprint`
    # — load-bearing ordering that nothing else protects, hence this assertion.
    assert _exec(c, "test", "-d", "/opt/camofox-browser/plugins/fingerprint").returncode == 0, \
        "auth should have the fingerprint plugin installed"
    assert _exec(c, "test", "-e", "/opt/camofox-flex/plugins/fingerprint").returncode != 0, \
        "flex must not carry the fingerprint-pinning plugin at all"


def test_flex_profiles_are_ephemeral_and_never_on_opt_data(everstone):
    """flex's profile dir is wiped at every start and lives outside the data volume."""
    c = everstone["container_name"]
    r = _exec(c, "sh", "-c", "grep -E '^export CAMOFOX_PROFILE_DIR=' /scripts/camofox-flex-run")
    assert "/run/camofox-flex" in r.stdout, r.stdout
    assert "/opt/data" not in r.stdout, "flex must never persist under the data volume"
    # and the run script must actually clear it (note /run is NOT a tmpfs in this image,
    # so the wipe is what provides ephemerality across restarts).
    wipe = _exec(c, "sh", "-c", 'grep -E "rm -rf .*CAMOFOX_PROFILE_DIR" /scripts/camofox-flex-run')
    assert wipe.returncode == 0, "flex run script must wipe its profile dir at start"

    auth_run = _exec(c, "sh", "-c", "grep -E '^export CAMOFOX_PROFILE_DIR=' /scripts/camofox-auth-run")
    assert "/opt/data/browser/profiles" in auth_run.stdout, auth_run.stdout


def test_flex_holds_no_authenticated_profile(everstone):
    """The authenticated storage state must not be reachable from the flex root/dir."""
    c = everstone["container_name"]
    found = _exec(c, "sh", "-c",
                  "find /run/camofox-flex /opt/camofox-flex -name 'storage-state.json' 2>/dev/null | head -5")
    assert found.stdout.strip() == "", f"flex side holds persisted sessions: {found.stdout}"


def test_instances_bind_localhost_only(everstone):
    """CAMOFOX_BIND_HOST is the name lib/config.js reads; CAMOFOX_HOST is silently ignored.

    Getting this wrong (as the pre-split script did) left the server on :: — every
    interface — so the guard is on the variable NAME, not just the value.
    """
    c = everstone["container_name"]
    for script in ("/scripts/camofox-flex-run", "/scripts/camofox-auth-run"):
        r = _exec(c, "sh", "-c", f"grep -E '^export CAMOFOX_BIND_HOST=' {script}")
        assert "127.0.0.1" in r.stdout, f"{script} must set CAMOFOX_BIND_HOST=127.0.0.1: {r.stdout}"
    logs = subprocess.run(["docker", "logs", c], capture_output=True, text=True)
    started = [json.loads(ln) for ln in (logs.stdout + logs.stderr).splitlines()
               if '"msg":"server started"' in ln]
    assert started, "no camofox 'server started' lines found"
    for ev in started:
        assert ev["host"] == "127.0.0.1", f"camofox bound {ev['host']} on port {ev.get('port')}"


# --- the consumers are wired to the right instance ----------------------------------

def test_env_routes_browser_tools_to_flex_and_es_to_auth(everstone):
    c = everstone["container_name"]
    env = _exec(c, "sh", "-c", "echo $CAMOFOX_URL; echo $CAMOFOX_AUTH_URL").stdout.split()
    assert env == ["http://localhost:9377", "http://localhost:9378"], env


def test_es_tools_target_the_auth_instance(everstone):
    """es owns the authenticated browser; pointing it at flex would break es_login silently."""
    c = everstone["container_name"]
    r = _exec(c, "/usr/local/lib/hermes-agent/.venv/bin/python", "-c",
              "import es.web_login as w; print(w._CAMOFOX)")
    assert r.stdout.strip() == AUTH, f"es resolved to {r.stdout.strip()!r}, want {AUTH}"


def test_hermes_browser_persistence_disabled(everstone):
    """flex is ephemeral, so a stable per-profile userId would imply durability it lacks."""
    c = everstone["container_name"]
    r = _exec(c, "sh", "-c",
              "grep -A3 'camofox:' /opt/data/hermes/profiles/everstone/config.yaml")
    assert "managed_persistence: false" in r.stdout, r.stdout
