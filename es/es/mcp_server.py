"""es MCP server — exposes es operations as MCP tools (FastMCP).

Wraps the same in-process clients the CLI uses; returns the same
{ok,data}/{ok,error} envelope. The CLI (es.main) stays for dev/tests; the
AGENT only ever sees these tools.
"""
import functools
from datetime import datetime, timedelta
from typing import Optional

from mcp.server.fastmcp import FastMCP
import httpx
import trafilatura

from es import config
from es.deeplink import build_deeplink
from es.google_auth import calendar_service, people_service
from es.tasks_client import TasksClient
from es.vault_client import VaultClient
from es.capabilities import cal as cal_cap
from es.capabilities import cal_support
from es.capabilities import maps as maps_cap

mcp = FastMCP("everstone-es")


def mcp_envelope(fn):
    """Turn a tool's return into {ok:true,data}; any exception into
    {ok:false,error:{code,message}} (mirrors the CLI @envelope). Honors an
    exception's `es_code` attribute for the error code."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except Exception as e:  # noqa: BLE001 — tool boundary: never raise to the agent
            code = getattr(e, "es_code", None) or type(e).__name__
            return {"ok": False, "error": {"code": code, "message": str(e)}}
    return wrapper


def _client():
    cfg = config.load_config()
    caldav = cfg.get("caldav") or {}
    vault = (cfg.get("obsidian") or {}).get("vault_name", "")
    return TasksClient(config.CALDAV_URL, caldav.get("user", ""), caldav.get("password", "")), vault


def _notes_client():
    cfg = config.load_config()
    obs = cfg.get("obsidian") or {}
    return VaultClient(config.vault_root(), obs.get("vault_name", ""),
                       journal_folder=obs.get("journal_folder", "Journal"),
                       categories=obs.get("categories") or ["Topics"],
                       attach_sources=config.attach_source_dirs(obs))


@mcp.tool()
@mcp_envelope
def es_tasks_list(list: str = "TODO", tag: Optional[str] = None, all: bool = False) -> list:
    """List tasks in a list (default TODO). all=true includes completed; tag filters."""
    client, _ = _client()
    items = client.list_tasks(list)
    if not all:
        items = [t for t in items if str(t.get("status", "")) != "COMPLETED"]
    if tag:
        items = [t for t in items if tag in (t.get("tags") or [])]
    return items


@mcp.tool()
@mcp_envelope
def es_tasks_add(summary: str, list: str = "TODO", note: Optional[str] = None,
                 tag: Optional[str] = None, due: Optional[str] = None,
                 remind: Optional[str] = None, parent: Optional[str] = None) -> dict:
    """Add a task to a list (default TODO). note attaches an Obsidian deeplink;
    due/remind are ISO datetimes; tag adds a single tag; parent nests as a subtask."""
    client, vault = _client()
    url = build_deeplink(vault, note) if note else None
    uid = client.add_task(
        summary, list, url=url,
        remind_at=datetime.fromisoformat(remind) if remind else None,
        due=datetime.fromisoformat(due) if due else None,
        tags=[tag] if tag else None,
        parent_uid=parent,
    )
    return {"uid": uid}


@mcp.tool()
@mcp_envelope
def es_tasks_edit(uid: str, list: str = "TODO", summary: Optional[str] = None,
                  tag: Optional[str] = None, due: Optional[str] = None,
                  remind: Optional[str] = None, parent: Optional[str] = None) -> dict:
    """Edit a task. Only provided fields change; due/remind are ISO datetimes;
    tag sets a single tag; parent re-nests the task."""
    client, _ = _client()
    client.edit_task(
        uid, list,
        summary=summary,
        due=datetime.fromisoformat(due) if due else None,
        remind_at=datetime.fromisoformat(remind) if remind else None,
        tags=[tag] if tag else None,
        parent_uid=parent,
    )
    return {"uid": uid, "edited": True}


@mcp.tool()
@mcp_envelope
def es_tasks_done(uid: str, list: str = "TODO") -> dict:
    """Mark a task complete."""
    client, _ = _client()
    client.complete_task(uid, list)
    return {"uid": uid, "status": "COMPLETED"}


@mcp.tool()
@mcp_envelope
def es_tasks_delete(uid: str, list: str = "TODO", force: bool = False) -> dict:
    """Delete a task. force=true deletes even when it has subtasks."""
    client, _ = _client()
    client.delete_task(uid, list, force=force)
    return {"uid": uid, "deleted": True}


@mcp.tool()
@mcp_envelope
def es_tasks_lists() -> list:
    """List all task lists (collections)."""
    client, _ = _client()
    return client.list_collections()


@mcp.tool()
@mcp_envelope
def es_tasks_list_create(name: str) -> dict:
    """Create a task list (no-op if it already exists)."""
    client, _ = _client()
    client.ensure_list(name)
    return {"list": name, "created": True}


@mcp.tool()
@mcp_envelope
def es_tasks_list_delete(name: str) -> dict:
    """Delete a task list."""
    client, _ = _client()
    client.delete_list(name)
    return {"list": name, "deleted": True}


@mcp.tool()
@mcp_envelope
def es_tasks_clear(list: str, all: bool = False) -> dict:
    """Remove completed tasks from a list. all=true removes every task."""
    client, _ = _client()
    removed = client.clear_list(list, completed_only=not all)
    return {"list": list, "removed": removed}


@mcp.tool()
@mcp_envelope
def es_cal_agenda(start: str, end: str, calendar: str, tz: Optional[str] = None) -> list:
    """List events on a calendar between start and end (YYYY-MM-DD or full ISO)."""
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    tmin, tmax = cal_cap._day_bounds(start, end, tzname)
    items = svc.events().list(
        calendarId=cal_id, timeMin=tmin, timeMax=tmax,
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    return [cal_cap._event_view(e, tzname) for e in items]


@mcp.tool()
@mcp_envelope
def es_cal_search(query: str, calendar: str, tz: Optional[str] = None) -> list:
    """Full-text search events on a calendar."""
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    items = svc.events().list(
        calendarId=cal_id, q=query, singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    return [cal_cap._event_view(e, tzname) for e in items]


@mcp.tool()
@mcp_envelope
def es_cal_conflicts(start: str, end: str, calendar: str, tz: Optional[str] = None) -> list:
    """Find overlapping event pairs on a calendar in the given window."""
    tzname = tz or cal_support.home_tz()
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    tmin, tmax = cal_cap._day_bounds(start, end, tzname)
    items = svc.events().list(
        calendarId=cal_id, timeMin=tmin, timeMax=tmax,
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    # chronological sweep (ref: gcalcli/conflicts.py): a pair conflicts when the
    # later event starts before the earlier one ends.
    out: list = []
    active: list = []
    for e in items:
        s = cal_cap._instant(e, "start")
        active = [a for a in active if cal_cap._instant(a, "end") > s]
        for a in active:
            out.append({"a": cal_cap._event_view(a, tzname), "b": cal_cap._event_view(e, tzname)})
        active.append(e)
    return out


@mcp.tool()
@mcp_envelope
def es_cal_add(summary: str, calendar: str, when: str, duration: int = 60,
               where: Optional[str] = None, description: Optional[str] = None,
               tz: Optional[str] = None) -> dict:
    """Create an event. when is 'YYYY-MM-DD HH:MM' (local); duration in minutes.
    Refused on read-only calendars."""
    cal_cap._require_writable(calendar)
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


@mcp.tool()
@mcp_envelope
def es_cal_edit(event_id: str, calendar: str, summary: Optional[str] = None,
                when: Optional[str] = None, duration: Optional[int] = None,
                where: Optional[str] = None, description: Optional[str] = None,
                tz: Optional[str] = None) -> dict:
    """Edit an event; only provided fields change. when is 'YYYY-MM-DD HH:MM'
    (local); duration in minutes. Refused on read-only calendars."""
    cal_cap._require_writable(calendar)
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
    return cal_cap._event_view(updated, tzname)


@mcp.tool()
@mcp_envelope
def es_cal_delete(event_id: str, calendar: str) -> dict:
    """Delete an event. Refused on read-only calendars."""
    cal_cap._require_writable(calendar)
    svc = calendar_service()
    cal_id = cal_support.resolve_calendar_id(svc, calendar)
    svc.events().delete(calendarId=cal_id, eventId=event_id).execute()
    return {"id": event_id, "deleted": True}


@mcp.tool()
@mcp_envelope
def es_notes_journal(title: str, body: str, tags: Optional[list] = None,
                     topics: Optional[list] = None, meta: Optional[dict] = None) -> dict:
    """Create one atomic journal entry (auto created/author); topics link topic docs
    via quoted wikilinks. Returns {path, obsidian_deeplink}."""
    return _notes_client().write_journal(title, body, tags=tags, topics=topics, meta=meta)


@mcp.tool()
@mcp_envelope
def es_notes_topic(name: str, body: Optional[str] = None,
                   update: Optional[str] = None, category: Optional[str] = None) -> dict:
    """Create/edit a topic doc. body overwrites the curated state; update appends a
    dated line under ## Updates. category files a NEW topic under an approved folder
    (default the first configured); an existing topic updates in place."""
    return _notes_client().write_topic(name, body=body, update=update, category=category)


@mcp.tool()
@mcp_envelope
def es_notes_topics(like: Optional[str] = None) -> list:
    """List canonical topic names (the registry); like fuzzy-matches. Use before
    creating a topic to resolve/dedup an existing one."""
    return _notes_client().list_topics(like=like)


@mcp.tool()
@mcp_envelope
def es_notes_attach(target: str, source: str) -> dict:
    """Copy a local file into the vault next to `target` (a topic name or a note path)
    and return {ref} — the path-qualified ![[…]] embed to place in the note body. Does
    NOT edit the note; the agent embeds the ref via es_notes_edit / es_notes_topic.
    source is a local path (copied in, original left in place); URLs are not fetched
    here. source must be a file already in the agent's media cache (a Telegram upload
    or agent-generated file) — paths outside the allowed cache dirs are rejected, since
    the file is copied into the synced vault."""
    return _notes_client().attach(target, source)


@mcp.tool()
@mcp_envelope
def es_notes_edit(target: str, body: Optional[str] = None,
                  append: Optional[str] = None) -> dict:
    """Edit an existing note (journal entry or topic; target is a note path or topic
    name). body overwrites the body (body='' clears it); append adds to it (frontmatter
    is preserved). Use append to embed an attachment ref returned by es_notes_attach."""
    return _notes_client().edit_note(target, body=body, append=append)


@mcp.tool()
@mcp_envelope
def es_notes_read(target: str) -> dict:
    """Read a note's frontmatter + body. target is a vault-relative path or a topic
    name. Returns {path, frontmatter, body}."""
    return _notes_client().read_note(target)


@mcp.tool()
@mcp_envelope
def es_notes_list(topic: Optional[str] = None, since: Optional[str] = None,
                  day: Optional[str] = None) -> list:
    """List journal entries (frontmatter summaries), filtered by topic link, since a
    date (YYYY-MM-DD), or a specific day."""
    return _notes_client().list_journal(topic=topic, since=since, day=day)


_WEB_FETCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_WEB_FETCH_TIMEOUT = 12.0
_WEB_FETCH_THIN_CHARS = 300
_WEB_FETCH_MAX_BYTES = 3_000_000


def _http_get(url: str):
    """Single seam for the HTTP GET (monkeypatched in tests)."""
    with httpx.Client(follow_redirects=True, timeout=_WEB_FETCH_TIMEOUT,
                      headers={"User-Agent": _WEB_FETCH_UA}) as client:
        return client.get(url)


@mcp.tool()
@mcp_envelope
def es_web_fetch(url: str) -> dict:
    """Fetch a URL and return its readable text (light; no browser, no key).
    Returns {url, title, text, status, thin}. An error or thin=true means the
    page couldn't be read lightly — escalate to the browser_* tools."""
    resp = _http_get(url)
    resp.raise_for_status()
    ctype = str(resp.headers.get("content-type", "")).lower()
    final_url = str(resp.url)
    if "text/html" not in ctype:
        return {"url": final_url, "title": "", "text": "", "status": resp.status_code,
                "thin": True, "note": f"non-HTML content ({ctype or 'unknown'}); not extracted"}
    html = resp.text[:_WEB_FETCH_MAX_BYTES]
    text = trafilatura.extract(html) or ""
    meta = trafilatura.extract_metadata(html)
    title = (getattr(meta, "title", "") or "") if meta else ""
    return {"url": final_url, "title": title, "text": text, "status": resp.status_code,
            "thin": len(text) < _WEB_FETCH_THIN_CHARS}


_CONTACTS_READ_MASK = "names,phoneNumbers,emailAddresses,addresses,organizations"


def _contact_view(person: dict) -> dict:
    """Flatten a People API Person into {name, phones, emails, addresses, org}."""
    names = person.get("names") or []
    orgs = person.get("organizations") or []
    return {
        "name": names[0].get("displayName", "") if names else "",
        "phones": [p.get("value", "") for p in person.get("phoneNumbers") or []],
        "emails": [e.get("value", "") for e in person.get("emailAddresses") or []],
        "addresses": [a.get("formattedValue", "") for a in person.get("addresses") or []],
        "org": orgs[0].get("name", "") if orgs else "",
    }


@mcp.tool()
@mcp_envelope
def es_contacts_search(query: str, max_results: int = 10) -> list:
    """Search the owner's Google contacts (read-only). Returns a list of
    {name, phones, emails, addresses, org}. Names/phones/emails/addresses are
    lists (a contact may have several)."""
    svc = people_service()
    # People API quirk: searchContacts needs a primed cache. Issue a best-effort
    # warm-up empty-query call first so the real query returns results.
    try:
        svc.people().searchContacts(
            query="", pageSize=1, readMask=_CONTACTS_READ_MASK,
        ).execute()
    except Exception:  # noqa: BLE001 — warm-up is best-effort; ignore failures
        pass
    resp = svc.people().searchContacts(
        query=query, pageSize=max_results, readMask=_CONTACTS_READ_MASK,
    ).execute()
    return [_contact_view(r["person"]) for r in resp.get("results", []) if r.get("person")]


@mcp.tool()
@mcp_envelope
def es_maps_geocode(query: str) -> dict:
    """Geocode an address/place text to {address, lat, lng, place_id}. Building block; returns
    null-ish if nothing matches. Needs maps.api_key in config."""
    return maps_cap.geocode(query)


@mcp.tool()
@mcp_envelope
def es_maps_search(query: str, near: Optional[str] = None, open_now: bool = False,
                   limit: Optional[int] = None, include_rating: bool = False) -> list:
    """Search places by text. `near` (address/place/'lat,lng') biases results — the agent supplies
    it (there is no built-in 'near me'). include_rating adds ratings (costs a higher API tier).
    Returns [{name, address, place_id, rating}]."""
    return maps_cap.search(query, near=near, open_now=open_now, limit=limit, include_rating=include_rating)


@mcp.tool()
@mcp_envelope
def es_maps_place(place_id: str) -> dict:
    """Place details for a place_id (from es_maps_search/geocode): {name, address, phone, hours, url}."""
    return maps_cap.place(place_id)


@mcp.tool()
@mcp_envelope
def es_maps_directions(origin: str, destination: str, mode: str = "DRIVE") -> dict:
    """Travel time + distance for origin->destination. mode: DRIVE|WALK|BICYCLE|TRANSIT.
    Returns {duration, distance, summary}. origin/destination are plain strings the agent supplies."""
    return maps_cap.directions(origin, destination, mode=mode)


@mcp.tool()
@mcp_envelope
def es_maps_distance_matrix(origins: list, destinations: list, mode: str = "DRIVE") -> list:
    """Travel time/distance for every origin->destination pair in ONE call — the decision tool
    ('which of these is closest/best'). Returns [{origin, destination, duration, distance, ok}].
    origins+destinations must be <= 50 (and <= 625 pairs). Agent supplies the location strings."""
    return maps_cap.distance_matrix(origins, destinations, mode=mode)


@mcp.tool()
@mcp_envelope
def es_login(profile: str = "maps") -> dict:
    """Prepare or confirm an interactive web login for an authenticated browser profile
    (e.g. "maps"). Idempotent: probes liveness (cheap cookie pre-check, then a live google.com
    browse); if signed in, closes the login window and returns {status:"logged_in"}; if not,
    opens the noVNC login window and returns {status:"awaiting_login", login_url}. Relay
    login_url to the user so THEY can sign in by hand (incl. 2FA); the agent never drives the
    browser. When the user says they're done, call this again to confirm + capture the session,
    then retry the original tool."""
    from es import web_login as wl
    cfg = config.load_config()
    public_url = (cfg.get("public_url") or "").rstrip("/")
    out = wl.run_es_login(
        profile,
        probe_home=wl.probe_home,
        capture=wl.fetch_state,   # GET storage_state also fires the persistence checkpoint
        open_signin=wl.open_signin,
        close_window=wl.close_window,
        login_url=wl.build_login_url(public_url),
    )
    # No polling: the user completing login → saying "done" → the agent re-calling this tool
    # (idempotent: it then probes, captures, and closes) IS the success path. We only arm a
    # fail-safe timer to remove the route if login is never completed, and cancel it once we've
    # confirmed logged_in (run_es_login already closed the window in that branch).
    if out.get("status") == "awaiting_login":
        wl.schedule_window_close()
    else:
        wl.cancel_window_close()
    return out


def main() -> None:
    mcp.run()
