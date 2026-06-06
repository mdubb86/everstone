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
    monkeypatch.setattr("es.capabilities.cal.cal_support.calendar_policy",
                        lambda: ({"Allison's Calendar"}, ["Family", "Michael's Calendar"]))
    monkeypatch.setattr("es.capabilities.cal.cal_support.home_tz", lambda: "America/Chicago")
    monkeypatch.setattr("es.capabilities.cal.cal_support.resolve_calendar_id",
                        lambda s, name: {"Family": "fam@g"}[name])
    return service


def test_agenda_returns_events_localized(svc):
    svc.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "e1", "summary": "Coffee",
                   "start": {"dateTime": "2026-06-08T14:00:00Z"},
                   "end": {"dateTime": "2026-06-08T15:00:00Z"}}]}
    res = runner.invoke(main.app, ["cal", "agenda", "2026-06-08", "2026-06-09",
                                   "--calendar", "Family"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)["data"]
    assert data[0]["summary"] == "Coffee"
    # 14:00Z == 09:00 America/Chicago (CDT)
    assert data[0]["start"].startswith("2026-06-08T09:00:00")


def test_search_passes_query(svc):
    svc.events.return_value.list.return_value.execute.return_value = {"items": []}
    runner.invoke(main.app, ["cal", "search", "dentist", "--calendar", "Family"])
    _, kwargs = svc.events.return_value.list.call_args
    assert kwargs["q"] == "dentist"
    assert kwargs["singleEvents"] is True
