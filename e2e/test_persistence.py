import subprocess
import time
import uuid

import caldav
import requests
from icalendar import Calendar as ICalendar
from icalendar import Todo


def _wait_for_health(base, container, timeout_iters=60):
    """Wait for /health AND s6-rc supervision tree AND couchdb to be up.

    /health is served by Caddy which can come up before its upstreams, so we
    additionally poll s6-rc-server and the couchdb /db/ endpoint to make sure
    the container is fully ready after a restart.
    """
    for _ in range(timeout_iters):
        try:
            ok = requests.get(f"{base}/health", timeout=1).status_code == 200
        except Exception:
            ok = False
        if ok:
            # Check s6-rc supervision tree is reachable
            rc = subprocess.run(
                ["docker", "exec", container, "s6-rc", "-a", "list"],
                capture_output=True,
                text=True,
            )
            if rc.returncode == 0:
                # Confirm couchdb is responding through Caddy
                try:
                    cr = requests.get(
                        f"{base}/db/",
                        auth=("testuser", "testpass"),
                        timeout=2,
                    )
                    if cr.status_code == 200:
                        return True
                except Exception:
                    pass
        time.sleep(2)
    return False


def _get_or_make_calendar(principal, name="inbox"):
    try:
        for c in principal.calendars():
            if (c.get_display_name() or "") == name:
                return c
    except Exception:
        pass
    return principal.make_calendar(
        name=name,
        cal_id=name,
        supported_calendar_component_set=["VTODO"],
    )


def test_vault_file_survives_restart(everstone):
    name = everstone["container_name"]
    marker = f"e2e-persist-{uuid.uuid4().hex[:8]}.md"
    subprocess.run(
        ["docker", "exec", name, "sh", "-c", f"echo persisted > /opt/data/vault/{marker}"],
        check=True,
    )
    subprocess.run(["docker", "restart", name], check=True)
    assert _wait_for_health(
        everstone["base_url"], everstone["container_name"]
    ), "container did not recover after restart"
    r = subprocess.run(
        ["docker", "exec", name, "test", "-f", f"/opt/data/vault/{marker}"]
    )
    assert r.returncode == 0


def test_tasks_survive_restart(everstone):
    base = everstone["base_url"]
    name = everstone["container_name"]
    client = caldav.DAVClient(
        url=f"{base}/caldav/", username="testcal", password="testcalpass"
    )
    principal = client.principal()
    cal = _get_or_make_calendar(principal, "inbox")

    uid = uuid.uuid4().hex
    ical = ICalendar()
    ical.add("prodid", "-//e2e//EN")
    ical.add("version", "2.0")
    todo = Todo()
    todo.add("uid", uid)
    todo.add("summary", "Survive restart")
    todo.add("status", "NEEDS-ACTION")
    ical.add_component(todo)
    cal.save_todo(ical=ical.to_ical().decode())

    subprocess.run(["docker", "restart", name], check=True)
    assert _wait_for_health(base, name), "container did not recover after restart"

    client2 = caldav.DAVClient(
        url=f"{base}/caldav/", username="testcal", password="testcalpass"
    )
    cal2 = _get_or_make_calendar(client2.principal(), "inbox")
    assert any(
        str(t.icalendar_component.get("uid", "")) == uid
        for t in cal2.todos(include_completed=True)
    ), f"uid {uid} not found after restart"


def test_backup_creates_tarball(everstone):
    name = everstone["container_name"]
    subprocess.run(["docker", "exec", name, "/scripts/backup"], check=True)
    r = subprocess.run(
        [
            "docker",
            "exec",
            name,
            "sh",
            "-c",
            "ls /opt/data/backups/everstone-*.tar.gz | head -1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    tarball = r.stdout.strip()
    assert tarball.endswith(".tar.gz"), f"unexpected tarball name: {tarball!r}"
    r2 = subprocess.run(
        ["docker", "exec", name, "tar", "-tzf", tarball],
        capture_output=True,
        text=True,
        check=True,
    )
    for top in ("couchdb/", "vault/", "radicale/", "hermes/"):
        assert any(line.startswith(top) for line in r2.stdout.splitlines()), top
