# es cal + Google Auth — Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `es cal` capability, talking to the Google Calendar API directly (dropping gcalcli), plus the shared Google-credential consumer that future Google capabilities reuse.

**Architecture:** `es/es/google_auth.py` loads the stored OAuth `Credentials`, refreshes if expired, and builds API service objects (consumer only — the OAuth *flow* stays operator-side, handled in Plan 3). `es/es/capabilities/cal.py` is a Typer sub-app (agenda/search/conflicts/add/edit/delete) using that service. Calendar **summary→id** resolution comes from `calendarList().list()`; read-only enforcement and timezone come from `config.yaml`. All output uses the Plan 1 JSON envelope.

**Tech Stack:** Python 3.12, `google-api-python-client`, `google-auth`, Typer, `zoneinfo`. Tests mock the service object — no network.

**Verified by spike (2026-06-06):** stored creds load + `creds.refresh(Request())` works; `build("calendar","v3",credentials=creds)` + `calendarList().list()` + `events().list(singleEvents=True, orderBy="startTime")` return real data. Reference implementation: `/usr/lib/python3.12/site-packages/gcalcli/{gcal.py,auth.py,conflicts.py}`.

**Prereq:** Plan 1 merged (`es` package, `es.config`, `es.output`, `es.runner.envelope`).

**Deferred to Plan 3:** generalizing the operator OAuth flow (`auth_gcal.py` → `everstone auth google`, union scopes), relocating the creds store off gcalcli's path, adding a `timezone` field to `config/schema.json`. Plan 2 reads the *existing* creds file + falls back to `America/Chicago`, so it builds and tests independently.

---

### Task 1: Google credential consumer (`google_auth.py`)

**Files:**
- Create: `es/es/google_auth.py`
- Test: `es/tests/test_google_auth.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_google_auth.py`:
```python
from unittest.mock import MagicMock, patch
from es import google_auth


def test_load_credentials_refreshes_when_expired(tmp_path, monkeypatch):
    creds = MagicMock()
    creds.valid = False
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(tmp_path / "oauth"))
    (tmp_path / "oauth").write_bytes(b"x")
    with patch("es.google_auth.pickle.load", return_value=creds), \
         patch("es.google_auth.Request"):
        out = google_auth.load_credentials()
    creds.refresh.assert_called_once()
    assert out is creds


def test_load_credentials_no_refresh_when_valid(tmp_path, monkeypatch):
    creds = MagicMock()
    creds.valid = True
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(tmp_path / "oauth"))
    (tmp_path / "oauth").write_bytes(b"x")
    with patch("es.google_auth.pickle.load", return_value=creds):
        google_auth.load_credentials()
    creds.refresh.assert_not_called()


def test_missing_creds_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(tmp_path / "nope"))
    import pytest
    with pytest.raises(FileNotFoundError):
        google_auth.load_credentials()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_google_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'es.google_auth'`

- [ ] **Step 3: Write the implementation**

`es/es/google_auth.py`:
```python
"""Shared Google credential consumer for es. Loads the stored OAuth
credential, refreshes it if expired, and builds API service objects.

The OAuth *flow* (browser consent) is an operator action handled elsewhere;
es only consumes the already-stored credential. Verified working against the
live Calendar API by the 2026-06-06 spike."""
import os
import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Current store is gcalcli's pickle file; Plan 3 relocates it. Override for tests.
_DEFAULT_CREDS_PATH = "/opt/data/hermes/gcalcli/oauth"


def _creds_path() -> Path:
    return Path(os.environ.get("ES_GOOGLE_CREDS_PATH", _DEFAULT_CREDS_PATH))


def load_credentials():
    path = _creds_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"es: Google not authorized (no creds at {path}). "
            f"Run the operator auth flow first."
        )
    with open(path, "rb") as fh:
        creds = pickle.load(fh)
    if not creds.valid:
        creds.refresh(Request())
    return creds


def calendar_service():
    return build("calendar", "v3", credentials=load_credentials(),
                 cache_discovery=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_google_auth.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add es/es/google_auth.py es/tests/test_google_auth.py
git commit -m "feat(es): shared Google credential consumer (load/refresh/service)"
```

---

### Task 2: cal config helpers (calendars, timezone, name→id)

**Files:**
- Create: `es/es/capabilities/cal_support.py`
- Test: `es/tests/test_cal_support.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_cal_support.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_cal_support.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'es.capabilities.cal_support'`

- [ ] **Step 3: Write the implementation**

`es/es/capabilities/cal_support.py`:
```python
"""Config + calendar-resolution helpers for es cal."""
from typing import List, Set, Tuple

from es import config

DEFAULT_TZ = "America/Chicago"


def calendar_policy() -> Tuple[Set[str], List[str]]:
    """Return (read_only set, read_write list) of calendar summaries from config."""
    cals = ((config.load_config().get("gcalcli") or {}).get("calendars") or {})
    read_only = set(cals.get("read_only") or [])
    read_write = list(cals.get("read_write") or [])
    return read_only, read_write


def home_tz() -> str:
    """Operator home timezone; Plan 3 adds the schema field. Falls back Central."""
    return config.load_config().get("timezone") or DEFAULT_TZ


def resolve_calendar_id(service, summary: str) -> str:
    """Map a calendar display name (summary) to its API id via calendarList."""
    items = service.calendarList().list().execute().get("items", [])
    for c in items:
        if c.get("summary") == summary:
            return c["id"]
    raise KeyError(f"calendar not found: {summary!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_cal_support.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add es/es/capabilities/cal_support.py es/tests/test_cal_support.py
git commit -m "feat(es): cal config + calendar-id resolution helpers"
```

---

### Task 3: cal read verbs — `agenda` and `search`

**Files:**
- Create: `es/es/capabilities/cal.py`
- Test: `es/tests/test_cal_read.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_cal_read.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_cal_read.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'es.capabilities.cal'`

- [ ] **Step 3: Write the implementation**

`es/es/capabilities/cal.py`:
```python
"""es cal — Google Calendar via the API directly (no gcalcli).

Reference: gcalcli/gcal.py (service/list/insert/patch/delete), conflicts.py.
Verified by the 2026-06-06 direct-API spike.
"""
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

import typer

from es.google_auth import calendar_service
from es.runner import envelope
from es.capabilities import cal_support

app = typer.Typer(no_args_is_help=True)

GROUP_SAFE = False
CONFIG_KEYS = ("gcalcli.calendars", "timezone")


def _localize(dt_str: str, tz: str) -> str:
    """RFC3339 dateTime -> ISO string in tz. Pass-through for all-day 'date'."""
    if "T" not in dt_str:           # all-day event ('date')
        return dt_str
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz)).isoformat()


def _event_view(e: dict, tz: str) -> dict:
    s = e.get("start", {})
    en = e.get("end", {})
    return {
        "id": e.get("id"),
        "summary": e.get("summary", ""),
        "start": _localize(s.get("dateTime") or s.get("date", ""), tz),
        "end": _localize(en.get("dateTime") or en.get("date", ""), tz),
        "location": e.get("location"),
    }


def _day_bounds(start: str, end: str, tz: str):
    """Accept YYYY-MM-DD (or full ISO); return RFC3339 timeMin/timeMax in tz."""
    z = ZoneInfo(tz)
    smin = datetime.fromisoformat(start) if "T" in start else datetime.fromisoformat(start + "T00:00:00")
    smax = datetime.fromisoformat(end) if "T" in end else datetime.fromisoformat(end + "T00:00:00")
    return smin.replace(tzinfo=z).isoformat(), smax.replace(tzinfo=z).isoformat()


@app.command("agenda")
@envelope
def agenda(ctx: typer.Context,
           start: str = typer.Argument(...),
           end: str = typer.Argument(...),
           calendar: str = typer.Option(..., "--calendar"),
           tz: Optional[str] = typer.Option(None, "--tz")):
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    tmin, tmax = _day_bounds(start, end, tzname)
    items = svc.events().list(
        calendarId=cal_id, timeMin=tmin, timeMax=tmax,
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    return [_event_view(e, tzname) for e in items]


@app.command("search")
@envelope
def search(ctx: typer.Context,
           query: str = typer.Argument(...),
           calendar: str = typer.Option(..., "--calendar"),
           tz: Optional[str] = typer.Option(None, "--tz")):
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    items = svc.events().list(
        calendarId=cal_id, q=query, singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    return [_event_view(e, tzname) for e in items]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_cal_read.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add es/es/capabilities/cal.py es/tests/test_cal_read.py
git commit -m "feat(es): cal agenda + search (direct Calendar API, tz-localized)"
```

---

### Task 4: cal `conflicts` verb

**Files:**
- Modify: `es/es/capabilities/cal.py` (add `conflicts` command + `_overlaps` helper)
- Test: `es/tests/test_cal_conflicts.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_cal_conflicts.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_cal_conflicts.py -v`
Expected: FAIL — `Error: No such command 'conflicts'`

- [ ] **Step 3: Add the conflicts command + helper to `cal.py`**

Append to `es/es/capabilities/cal.py`:
```python
def _instant(e: dict, key: str) -> str:
    """Comparable RFC3339 instant for ordering/overlap (UTC normalized)."""
    v = e.get(key, {})
    raw = v.get("dateTime") or (v.get("date", "") + "T00:00:00+00:00")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ZoneInfo("UTC")).isoformat()


@app.command("conflicts")
@envelope
def conflicts(ctx: typer.Context,
              start: str = typer.Argument(...),
              end: str = typer.Argument(...),
              calendar: str = typer.Option(..., "--calendar"),
              tz: Optional[str] = typer.Option(None, "--tz")):
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    tmin, tmax = _day_bounds(start, end, tzname)
    items = svc.events().list(
        calendarId=cal_id, timeMin=tmin, timeMax=tmax,
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    # chronological sweep (ref: gcalcli/conflicts.py): a pair conflicts when the
    # later event starts before the earlier one ends.
    out: List[dict] = []
    active: List[dict] = []
    for e in items:
        s = _instant(e, "start")
        active = [a for a in active if _instant(a, "end") > s]
        for a in active:
            out.append({"a": _event_view(a, tzname), "b": _event_view(e, tzname)})
        active.append(e)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_cal_conflicts.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add es/es/capabilities/cal.py es/tests/test_cal_conflicts.py
git commit -m "feat(es): cal conflicts (chronological overlap sweep)"
```

---

### Task 5: cal write verbs — `add` / `edit` / `delete` with read-only enforcement

**Files:**
- Modify: `es/es/capabilities/cal.py` (add `add`/`edit`/`delete` + `_require_writable`)
- Test: `es/tests/test_cal_write.py`

- [ ] **Step 1: Write the failing test**

`es/tests/test_cal_write.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd es && python -m pytest tests/test_cal_write.py -v`
Expected: FAIL — `Error: No such command 'add'`

- [ ] **Step 3: Add write commands + the read-only guard to `cal.py`**

Append to `es/es/capabilities/cal.py`:
```python
class ReadOnlyCalendar(Exception):
    """Raised when a write targets a read-only-by-policy calendar."""


def _require_writable(calendar: str) -> None:
    read_only, _ = cal_support.calendar_policy()
    if calendar in read_only:
        raise ReadOnlyCalendar(
            f"{calendar!r} is read-only by policy; writes are refused. "
            f"Use a writable calendar instead."
        )


@app.command("add")
@envelope
def add(ctx: typer.Context,
        summary: str = typer.Argument(...),
        calendar: str = typer.Option(..., "--calendar"),
        when: str = typer.Option(..., "--when", help="YYYY-MM-DD HH:MM (local)"),
        duration: int = typer.Option(60, "--duration", help="minutes"),
        where: Optional[str] = typer.Option(None, "--where"),
        description: Optional[str] = typer.Option(None, "--description"),
        tz: Optional[str] = typer.Option(None, "--tz")):
    _require_writable(calendar)
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    start = datetime.fromisoformat(when.replace(" ", "T"))
    end = start + timedelta(minutes=duration)
    body = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": tzname},
        "end": {"dateTime": end.isoformat(), "timeZone": tzname},
    }
    if where:
        body["location"] = where
    if description:
        body["description"] = description
    created = svc.events().insert(calendarId=cal_id, body=body).execute()
    return {"id": created.get("id"), "summary": summary}


@app.command("edit")
@envelope
def edit(ctx: typer.Context,
         event_id: str = typer.Argument(...),
         calendar: str = typer.Option(..., "--calendar"),
         summary: Optional[str] = typer.Option(None, "--summary"),
         when: Optional[str] = typer.Option(None, "--when"),
         duration: Optional[int] = typer.Option(None, "--duration"),
         where: Optional[str] = typer.Option(None, "--where"),
         description: Optional[str] = typer.Option(None, "--description"),
         tz: Optional[str] = typer.Option(None, "--tz")):
    _require_writable(calendar)
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    patch: dict = {}
    if summary is not None:
        patch["summary"] = summary
    if where is not None:
        patch["location"] = where
    if description is not None:
        patch["description"] = description
    if when is not None:
        start = datetime.fromisoformat(when.replace(" ", "T"))
        patch["start"] = {"dateTime": start.isoformat(), "timeZone": tzname}
        if duration is not None:
            end = start + timedelta(minutes=duration)
            patch["end"] = {"dateTime": end.isoformat(), "timeZone": tzname}
    updated = svc.events().patch(calendarId=cal_id, eventId=event_id, body=patch).execute()
    return _event_view(updated, tzname)


@app.command("delete")
@envelope
def delete(ctx: typer.Context,
           event_id: str = typer.Argument(...),
           calendar: str = typer.Option(..., "--calendar")):
    _require_writable(calendar)
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    svc.events().delete(calendarId=cal_id, eventId=event_id).execute()
    return {"id": event_id, "deleted": True}
```

Also update the `envelope` error code for the read-only case so it is stable
(not the class name). Modify `es/es/runner.py` `wrapper` except clause:
```python
        except Exception as e:  # noqa: BLE001 - CLI boundary: never leak a traceback
            code = getattr(e, "es_code", None) or type(e).__name__
            raise typer.Exit(output.emit_error(code, str(e), _pretty(ctx)))
```
And give the exception its stable code — change `ReadOnlyCalendar` in `cal.py`:
```python
class ReadOnlyCalendar(Exception):
    es_code = "read_only_calendar"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd es && python -m pytest tests/test_cal_write.py tests/test_main.py -v`
Expected: PASS (read-only refusals use code `read_only_calendar`; `test_main.py` still green since `es_code` is read via `getattr`)

- [ ] **Step 5: Commit**

```bash
git add es/es/capabilities/cal.py es/es/runner.py es/tests/test_cal_write.py
git commit -m "feat(es): cal add/edit/delete with read-only enforcement + stable error codes"
```

---

### Task 6: Dependencies, mount, full suite, live smoke (read-only)

**Files:**
- Modify: `es/pyproject.toml` (add Google deps)
- Modify: `es/es/main.py` (mount `cal`)
- Test: full suite + a read-only live smoke

- [ ] **Step 1: Add Google deps to `es/pyproject.toml`**

Change the `dependencies` line:
```toml
dependencies = [
  "typer>=0.12", "pyyaml>=6", "everstone-tasks",
  "google-api-python-client>=2", "google-auth>=2",
]
```

- [ ] **Step 2: Mount the cal sub-app in `es/es/main.py`**

Add the import + mount (next to the tasks import/mount):
```python
from es.capabilities import cal
...
app.add_typer(cal.app, name="cal", help="Google Calendar (direct API).")
```

- [ ] **Step 3: Reinstall + run the whole suite**

Run:
```bash
cd es && pip install -e . && python -m pytest -v
```
Expected: PASS (output/config/main/tasks/google_auth/cal_support/cal_read/cal_conflicts/cal_write)

- [ ] **Step 4: Live read-only smoke inside the container (real API, no writes)**

Run:
```bash
docker cp es everstone:/opt/es-staging && \
docker exec everstone sh -c 'cd /opt/es-staging && pip install -e . -q && es cal agenda 2026-06-08 2026-06-10 --calendar Family --pretty'
```
Expected: a `{"ok": true, "data": [ … events … ]}` envelope with **Central** times (e.g. `09:00`), proving the direct-API path + tz localization end-to-end. (Read-only; safe.)

- [ ] **Step 5: Commit**

```bash
git add es/pyproject.toml es/es/main.py
git commit -m "feat(es): wire cal sub-app + Google deps; live agenda smoke passes"
```

---

## Self-Review

- **Spec coverage (Plan 2 slice):** ✅ direct Calendar API / gcalcli dropped (Task 1,3); shared Google credential consumer (Task 1); name→id resolution (Task 2); tz default + `--tz` (Task 2,3,5); verbs agenda/search/conflicts/add/edit/delete (Task 3,4,5); read-only enforcement (Task 5); JSON envelope (all, via `@envelope`); `group_safe=False` declared (Task 3). Deferred to Plan 3 (stated in header): operator `everstone auth google` flow + union scopes, creds-store relocation, `timezone` schema field.
- **Placeholder scan:** none — every step has complete code/commands.
- **Type consistency:** `calendar_service()` (google_auth) is monkeypatched in cal tests at `es.capabilities.cal.calendar_service`; `cal_support.{calendar_policy,home_tz,resolve_calendar_id}` signatures match their tests and call sites; `_event_view(e, tz)` shape `{id,summary,start,end,location}` is identical across agenda/search/conflicts/edit; the read-only path raises `ReadOnlyCalendar` (stable `es_code="read_only_calendar"`) consumed by the updated `runner.envelope`.
- **Note for executor:** the `runner.py` `es_code` change in Task 5 is required before `test_cal_write.py` passes; keep `ctx` first on every command for `@envelope`.
