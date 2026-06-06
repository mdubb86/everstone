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
    monkeypatch.setattr("es.capabilities.cal.cal_support.resolve_calendar_id",
                        lambda s, name: "fam@g")
    return service


def test_conflicts_finds_overlapping_pair(svc):
    svc.events.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "a", "summary": "A", "start": {"dateTime": "2026-06-08T14:00:00Z"},
         "end": {"dateTime": "2026-06-08T15:00:00Z"}},
        {"id": "b", "summary": "B", "start": {"dateTime": "2026-06-08T14:30:00Z"},
         "end": {"dateTime": "2026-06-08T15:30:00Z"}},
    ]}
    res = runner.invoke(main.app, ["cal", "conflicts", "2026-06-08", "2026-06-09",
                                   "--calendar", "Family"])
    pairs = json.loads(res.stdout)["data"]
    assert len(pairs) == 1
    assert {pairs[0]["a"]["id"], pairs[0]["b"]["id"]} == {"a", "b"}


def test_conflicts_none_when_disjoint(svc):
    svc.events.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "a", "summary": "A", "start": {"dateTime": "2026-06-08T14:00:00Z"},
         "end": {"dateTime": "2026-06-08T15:00:00Z"}},
        {"id": "b", "summary": "B", "start": {"dateTime": "2026-06-08T16:00:00Z"},
         "end": {"dateTime": "2026-06-08T17:00:00Z"}},
    ]}
    res = runner.invoke(main.app, ["cal", "conflicts", "2026-06-08", "2026-06-09",
                                   "--calendar", "Family"])
    assert json.loads(res.stdout)["data"] == []
