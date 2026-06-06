from unittest.mock import MagicMock
from es.capabilities import cal_support


def test_calendars_from_config(monkeypatch):
    monkeypatch.setattr(cal_support.config, "load_config", lambda: {
        "gcalcli": {"calendars": {"read_only": ["Allison's Calendar"],
                                   "read_write": ["Family", "Michael's Calendar"]}}})
    ro, rw = cal_support.calendar_policy()
    assert ro == {"Allison's Calendar"}
    assert rw == ["Family", "Michael's Calendar"]


def test_home_tz_falls_back_to_central(monkeypatch):
    monkeypatch.setattr(cal_support.config, "load_config", lambda: {})
    assert cal_support.home_tz() == "America/Chicago"


def test_home_tz_from_config(monkeypatch):
    monkeypatch.setattr(cal_support.config, "load_config", lambda: {"timezone": "America/New_York"})
    assert cal_support.home_tz() == "America/New_York"


def test_resolve_calendar_id_matches_summary():
    svc = MagicMock()
    svc.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Family", "id": "fam@g"}, {"summary": "Michael's Calendar", "id": "m@g"}]}
    assert cal_support.resolve_calendar_id(svc, "Family") == "fam@g"


def test_resolve_calendar_id_unknown_raises():
    svc = MagicMock()
    svc.calendarList.return_value.list.return_value.execute.return_value = {"items": []}
    import pytest
    with pytest.raises(KeyError):
        cal_support.resolve_calendar_id(svc, "Nope")
