import json
from unittest.mock import MagicMock
import pytest
from typer.testing import CliRunner
from es import main

runner = CliRunner()


@pytest.fixture
def svc(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr("es.capabilities.cal.calendar_service", lambda: service)
    monkeypatch.setattr("es.capabilities.cal.cal_support.home_tz", lambda: "America/Chicago")
    monkeypatch.setattr("es.capabilities.cal.cal_support.calendar_policy",
                        lambda: ({"Allison's Calendar"}, ["Family", "Michael's Calendar"]))
    monkeypatch.setattr("es.capabilities.cal.cal_support.resolve_calendar_id",
                        lambda s, name: {"Family": "fam@g", "Allison's Calendar": "al@g"}[name])
    return service


def test_add_refused_on_readonly_calendar(svc):
    res = runner.invoke(main.app, ["cal", "add", "X", "--calendar", "Allison's Calendar",
                                   "--when", "2026-06-10 09:00"])
    assert res.exit_code == 1
    body = json.loads(res.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only_calendar"
    svc.events.return_value.insert.assert_not_called()


def test_add_inserts_with_tz(svc):
    svc.events.return_value.insert.return_value.execute.return_value = {"id": "new1"}
    res = runner.invoke(main.app, ["cal", "add", "Coffee", "--calendar", "Family",
                                   "--when", "2026-06-10 09:00", "--duration", "30",
                                   "--where", "Pinehouse"])
    assert json.loads(res.stdout)["data"]["id"] == "new1"
    _, kwargs = svc.events.return_value.insert.call_args
    body = kwargs["body"]
    assert body["summary"] == "Coffee"
    assert body["location"] == "Pinehouse"
    assert body["start"] == {"dateTime": "2026-06-10T09:00:00", "timeZone": "America/Chicago"}
    assert body["end"] == {"dateTime": "2026-06-10T09:30:00", "timeZone": "America/Chicago"}


def test_delete_refused_on_readonly(svc):
    res = runner.invoke(main.app, ["cal", "delete", "eid", "--calendar", "Allison's Calendar"])
    assert json.loads(res.stdout)["error"]["code"] == "read_only_calendar"
    svc.events.return_value.delete.assert_not_called()


def test_delete_calls_api(svc):
    svc.events.return_value.delete.return_value.execute.return_value = {}
    res = runner.invoke(main.app, ["cal", "delete", "eid", "--calendar", "Family"])
    assert json.loads(res.stdout)["data"] == {"id": "eid", "deleted": True}
    _, kwargs = svc.events.return_value.delete.call_args
    assert kwargs == {"calendarId": "fam@g", "eventId": "eid"}
