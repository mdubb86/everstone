import subprocess
import uuid
from urllib.parse import parse_qs, unquote, urlparse

import caldav
from icalendar import Calendar as ICalendar
from icalendar import Todo


def _write_note(container, vault_path, content="hello"):
    subprocess.run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"mkdir -p $(dirname /opt/data/vault/{vault_path}) && "
            f"printf '%s' '{content}' > /opt/data/vault/{vault_path}",
        ],
        check=True,
    )


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


def test_caldav_create_and_list(everstone):
    base = everstone["base_url"]
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
    todo.add("summary", "Buy milk")
    todo.add("status", "NEEDS-ACTION")
    todo.add("url", "obsidian://open?vault=testvault&file=Notes%2Fx.md")
    ical.add_component(todo)
    cal.save_todo(ical=ical.to_ical().decode())

    todos = cal.todos(include_completed=True)
    assert any(
        str(t.icalendar_component.get("uid", "")) == uid for t in todos
    ), f"uid {uid} not found in list"


def test_deeplink_resolves_to_vault_file(everstone):
    _write_note(everstone["container_name"], "Notes/landed.md", "landed content")
    deeplink = "obsidian://open?vault=testvault&file=Notes%2Flanded.md"
    qs = parse_qs(urlparse(deeplink).query)
    file_in_vault = unquote(qs["file"][0])
    r = subprocess.run(
        [
            "docker",
            "exec",
            everstone["container_name"],
            "test",
            "-f",
            f"/opt/data/vault/{file_in_vault}",
        ],
    )
    assert r.returncode == 0
