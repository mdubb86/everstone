"""es MCP server — exposes es operations as MCP tools (FastMCP).

Wraps the same in-process clients the CLI uses; returns the same
{ok,data}/{ok,error} envelope. The CLI (es.main) stays for dev/tests; the
AGENT only ever sees these tools.
"""
import functools
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generic, List, Optional, Tuple, TypeVar

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
import httpx
import trafilatura

from es import config
from es import url_guard
from es.deeplink import build_deeplink
from es.google_auth import calendar_service, people_service
from es.tasks_client import TasksClient
from es.vault_client import VaultClient
from es.capabilities import cal as cal_cap
from es.capabilities import cal_support
from es.capabilities import clock as clock_cap
from es.capabilities import doc_support
from es.capabilities import docs as docs_cap
from es.capabilities import maps as maps_cap
from es.capabilities import maps_write
from es.capabilities import read as read_cap
from es.capabilities import reader
from es.capabilities import weather as weather_cap

mcp = FastMCP("everstone-es")

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str


class Envelope(BaseModel, Generic[T]):
    """Typed form of the {ok,data}/{ok,error} envelope.

    Annotating a tool `-> Envelope[Model]` makes FastMCP publish a full MCP
    outputSchema (with $defs for nested models) instead of the untyped JSON an
    unannotated `-> dict` produces. Note the annotation MUST describe the
    envelope, not the payload: mcp_envelope wraps the return, and FastMCP
    validates the actual value against the published schema — so `-> Model`
    raises ToolError at call time.
    """
    ok: bool
    data: Optional[T] = None
    error: Optional[ErrorDetail] = None


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
    """Create an event. `when` is 'YYYY-MM-DD HH:MM' — WALL-CLOCK TIME AT THE EVENT'S LOCATION,
    not the operator's. duration in minutes. Refused on read-only calendars.

    SET `tz` (IANA, e.g. 'America/Los_Angeles') WHENEVER THE EVENT IS NOT IN THE OPERATOR'S HOME
    TIMEZONE — infer it from `where`. Without it the event silently lands in the home zone: a 3pm
    San Francisco meeting becomes 3pm Central, i.e. 1pm Pacific, two hours wrong. If unsure of a
    location's zone, es_maps_geocode(query, include_timezone=True) returns it."""
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
def es_notes_list(topic: Optional[str] = None, since: Optional[str] = None,
                  day: Optional[str] = None) -> list:
    """List journal entries (frontmatter summaries), filtered by topic link, since a
    date (YYYY-MM-DD), or a specific day."""
    return _notes_client().list_journal(topic=topic, since=since, day=day)


_WEB_FETCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_WEB_FETCH_TIMEOUT = 12.0
_WEB_FETCH_THIN_CHARS = 300
# Input cap for the HTML branch, sized for what's reasonable to hand lxml/
# trafilatura to parse — NOT an output limit (its output is a few KB of prose).
_WEB_FETCH_MAX_BYTES = 3_000_000


def _content_type_base(ctype: str) -> str:
    """The bare media type ("text/html"), stripping `; charset=...` and any
    other parameters. Content-Type is a structured header, not a substring
    bag — matching the raw header (e.g. "text/html" in ctype) also matches
    "application/json; profile=\"text/html\"" or a server-chosen fallback
    param naming text/html, silently routing the wrong body through the wrong
    branch. Parsed once here so both dispatch checks agree."""
    return ctype.split(";", 1)[0].strip()


def _guard_request_hook(request) -> None:
    """httpx request event hook — runs for the initial request AND for every
    redirect httpx follows, so a public URL that redirects to an internal one
    is refused at the hop rather than after the fact."""
    url_guard.check_url(str(request.url))


def _http_get(url: str):
    """Single seam for the HTTP GET (monkeypatched in tests)."""
    url_guard.check_url(url)     # explicit, so the refusal is not hook-dependent
    with httpx.Client(follow_redirects=True, timeout=_WEB_FETCH_TIMEOUT,
                      headers={"User-Agent": _WEB_FETCH_UA},
                      event_hooks={"request": [_guard_request_hook]}) as client:
        return client.get(url)


@mcp.tool()
@mcp_envelope
def es_web_fetch(url: str) -> dict:
    """Fetch a URL (light; no browser, no key). Returns readable text
    extracted from web pages; other content types are not extracted
    (thin=true, see note). Returns {url, title, text, status, thin,
    content_type, note}. Internal/private addresses are refused."""
    resp = _http_get(url)
    resp.raise_for_status()
    ctype = str(resp.headers.get("content-type", "")).lower()
    ctype_base = _content_type_base(ctype)
    final_url = str(resp.url)
    out = {"url": final_url, "title": "", "text": "", "status": resp.status_code,
           "thin": True, "content_type": ctype, "note": ""}

    if ctype_base == "text/html":
        html = resp.text[:_WEB_FETCH_MAX_BYTES]
        text = trafilatura.extract(html) or ""
        meta = trafilatura.extract_metadata(html)
        out["title"] = (getattr(meta, "title", "") or "") if meta else ""
        out["text"] = text
        out["thin"] = len(text) < _WEB_FETCH_THIN_CHARS
        return out

    out["note"] = f"non-HTML content ({ctype or 'unknown'}); not extracted"
    return out


def _doc_cache_root() -> Path:
    """Hermes's document cache for the active profile — where inbound uploads
    land and where our converted artifacts live beside them. Verified live:
    Telegram media goes to the PROFILE cache, not $HERMES_HOME/cache."""
    home = os.environ.get("HERMES_HOME", "/opt/data/hermes")
    return Path(home) / "profiles" / "everstone" / "cache" / "documents"


def _doc_roots():
    cfg = config.load_config()
    return config.readable_source_dirs(cfg.get("obsidian") or {})


@mcp.tool()
@mcp_envelope
def es_doc_extract(source: str, image_pages: Optional[str] = None) -> dict:
    """Convert a document — .pdf, .docx, .xlsx, .txt, .md, .csv, .json, or
    .ics — and return a HANDLE, never the document itself. source is the
    local path of a file the user uploaded (an absolute path) or a file in
    the vault, given as "$vault/..." or a vault-relative path (e.g.
    "Topics/Manual.pdf", same convention as es_read). Always converts (and
    caches) the whole document — to read only part of it, extract here once
    and then page through the result, not by narrowing what gets extracted.

    WHICH KIND OF THING YOU GET BACK depends on the format, and the receipt's
    `kind` says which:

    - A SPREADSHEET or CSV (.xlsx, .csv) becomes a QUERYABLE DATABASE, not
      text. kind is "table" and the receipt carries `tables` — for each
      sheet, the table name it became, its columns and types, its row count,
      and which row the header was detected on. There is no preview and
      nothing to read: ask it questions with es_doc_query and SQL. es_read
      does not work on these handles and will tell you so.
    - EVERYTHING ELSE becomes Markdown you page through with es_read (e.g.
      section="page-37"), and the receipt carries a short `preview` plus the
      keys described below.

    image_pages is a FALLBACK, not a normal step — skip it on a first call.
    Every embedded image and chart a PDF contains is already extracted and
    linked inline in the text you get back, so there is nothing left for it
    to reveal about a page's IMAGES. It exists for the opposite problem: the
    document's TEXT itself came back unreadable in a way rereading it won't
    fix — columns that got interleaved into nonsense, a table or form whose
    layout the extractor flattened, a page you can tell is broken just by
    looking at what came back. In that case, name the page(s) as they
    actually printed and look at them directly. PDF only, e.g. image_pages=
    "7" or "1-5" or "3,9" — there is no "render the whole document" default;
    name only the page(s) whose text you distrust. This does not create a
    second document: doc_id is unchanged, and calling it again on the same
    source (with or without image_pages) is a conversion cache hit that at
    most does the extra rendering work.

    For a table document, returns {doc_id, kind, tables, next} — and nothing
    else, because a preview of a database means nothing. Otherwise returns
    {doc_id, kind, page_count, preview, complete, page_images, next}.
    `preview` is only the first ~800 characters — enough to tell what you're
    holding, not to read it. `complete: true` means preview IS the whole
    document and nothing else need be called for the text. `page_images` is
    the list of rendered PNG paths — always present, empty when image_pages
    was not given. PDF pages that are images rather than text are still
    converted to inline ![page N](path) links in the full document — read
    those with vision_analyze once you reach them via es_read. `next` names
    the next call: when image_pages was given, it points at vision_analyze
    on the paths in `page_images`; otherwise it points at es_read (the
    "doc:<doc_id>" handle) to read the rest, paged by heading."""
    return docs_cap.extract(source, _doc_roots(), _doc_cache_root(),
                             image_pages=image_pages)


@mcp.tool()
@mcp_envelope
def es_doc_query(target: str, sql: str) -> dict:
    """Ask a question of a spreadsheet or CSV that es_doc_extract converted,
    using SQL. target is the "doc:<id>" handle that call returned.

    ANSWER THE QUESTION IN SQL — do not list rows and count them yourself.
    "How many transactions over $500 in September" is one
    SELECT count(*), sum(amount) ... WHERE, returning one row. A SELECT * is
    almost always the wrong call: it moves the reading problem instead of
    solving it, and only the first 200 rows come back anyway (`truncated`
    says so when there were more).

    The table names, their columns and types, and which sheet each came from
    are all in the es_doc_extract receipt — use them; do not guess a table
    name from the sheet name, since "Q1 Sales" becomes q1_sales and a second
    sheet that slugifies the same way becomes q1_sales_2. If you no longer
    have the receipt, `SHOW TABLES` and `DESCRIBE <table>` both work, and
    `SELECT * FROM tables_meta` gives the sheet-to-table mapping plus the
    header row that was detected for each.

    Two extra tables exist alongside the data. `tables_meta` is that mapping.
    `cells` holds EVERY non-empty cell of every sheet as raw text —
    (sheet, row, col, ref, value), where ref is the spreadsheet address the
    user sees ("B7", "AA12"). Reach for `cells` when a column looks wrong:
    header detection on a messy sheet is a guess, so if a column came back
    all-NULL, or the column names look like data, cross-check
    tables_meta.header_row against what that row actually holds in `cells`,
    and read the real values from there.

    Read-only: only SELECT (plus DESCRIBE / SHOW TABLES / SUMMARIZE), one
    statement per call. INSERT, UPDATE, DELETE, DROP, CREATE, COPY and ATTACH
    are all refused and change nothing. Returns {columns, rows, row_count,
    truncated}. A query still running after 15 seconds is stopped — narrow it
    or aggregate rather than retrying it unchanged."""
    resolved = reader.resolve_table(target, _doc_cache_root())
    return docs_cap.query_tables(resolved["adir"], sql)


# es_read's own whole-vs-outline threshold — for a DOCUMENT (kind == "doc":
# something es_doc_extract converted from outside the vault). Deliberately
# smaller than read.DEFAULT_WINDOW_LIMIT's own ~16,000-character "a window's
# worth of lines" estimate — a document built from many SMALL units (the
# motivating case: a 100+-event calendar where each event is only a couple dozen
# characters) needs to cross this threshold reliably, and a bound sized only
# for "large in total characters" would let exactly that document slip
# through as "small enough to return whole", one heading at a time, 100+
# times. A document arrives from outside at whatever size its source format
# happens to be (a scanned PDF, an exported calendar feed) — nothing about
# its length says anything about how much of it is worth seeing at once, so
# this stays tight.
_WHOLE_DOCUMENT_CHAR_LIMIT = 4_000

# The same threshold for a NOTE (kind == "note": something the user wrote
# into the vault themselves, via es_notes_journal/es_notes_topic/Obsidian
# directly) — deliberately much larger than _WHOLE_DOCUMENT_CHAR_LIMIT above.
#
# A note is authored, not ingested: its length reflects how much the user
# actually wrote, not an external format's page count, so "long" doesn't
# carry the same "there is a lot here to page through" implication it does
# for a converted document. The question a note-outline answers ("what did
# I write about the tournament") is usually better served by the answer
# itself than by a menu of section ids to fetch one at a time — an outline
# is a genuinely useful detour for a 40-page manual; for a topic note the
# user has been appending to for a year, it is mostly friction, costing the
# agent a second call for content that would have fit in the first response.
# A note that DOES have its own headings still gets one via `outline` below
# (nothing here suppresses that) — this only changes when a note crosses
# from "one answer" to "worth paging" in the first place.
#
# Set to read.DEFAULT_WINDOW_LIMIT's own ~16,000-character "a window's worth
# of lines" estimate (see that module's docstring: 200 lines at a typical
# prose width) rather than an arbitrary multiple of the document threshold:
# below that size, the note would fit in a single page/window anyway if it
# ever DID need paging, so returning it whole costs nothing paging would
# have saved. Above it, a long-appended note benefits from the same
# heading/line paging a document does — the risk this whole task exists to
# avoid (dumping an enormous blob into one response) is still real for a
# note, just at a size ordinary journal/topic writing rarely reaches.
_WHOLE_NOTE_CHAR_LIMIT = 16_000


# The hard cap on `content` — the ONE thing that stood between es_read and
# Hermes's own house limit (DEFAULT_MCP_RESULT_SIZE_CHARS = 50_000, the size
# past which a tool result is spilled to a file with only a preview kept in
# context — a file this agent has no tool to open). Every path that returns
# text (preamble/first-section, an explicit `section`, and a line `window`)
# must run through this before it reaches the envelope.
#
# The design spec named 40,000 ("chosen to sit under Hermes's 50,000 house
# limit with headroom for the envelope"), but that number priced in only
# `content` plus a small fixed envelope — not `outline`, which can itself be
# sizeable: a 100+-section document (the motivating case for the whole
# outline-vs-whole design) serializes to several thousand characters of its
# own ({id, title, level} per heading, JSON quoting included), and frontmatter
# adds more still for a note. Reusing 40,000 here would let a large `content`
# and a large `outline` add up past 50,000 in exactly the case (a big,
# many-section document) where both are most likely to be large at once.
# 32,000 keeps a deliberate ~18,000-character margin for outline + envelope +
# frontmatter overhead, while still comfortably exceeding what a single
# `es_read` call needs to be useful (the window default of 200 lines is
# itself sized to well under this).
_CONTENT_CHAR_CAP = 32_000


def _cap_content(text: str, resume_hint: str) -> str:
    """Cut `text` to at most _CONTENT_CHAR_CAP characters and say so in-band
    — the same "*(truncated ...)*" convention every doc_* converter's own
    self-truncation already uses (built through the shared
    doc_support.truncation_marker, so detection/wording stays one
    convention rather than a second, divergent one for es_read).

    Cuts at the last newline at-or-before the cap so a huge but ordinarily-
    line-broken text (the 112k-character, one-paragraph-per-line note this
    fix exists for) never loses a partial line — falling back to a hard cut
    only when no newline exists before the cap at all (e.g. one gigantic
    unbroken paragraph). `resume_hint` names how the agent gets the rest;
    callers here all point at the same escape hatch (`offset`), since a
    single returned section/preview has no paging concept of its own.
    Returns `text` unchanged when it's already within the cap.
    """
    if len(text) <= _CONTENT_CHAR_CAP:
        return text
    marker = "\n\n" + doc_support.truncation_marker(
        f"at {_CONTENT_CHAR_CAP} characters — {resume_hint}")
    # Reserve room for the marker itself: cutting AT the cap and appending
    # the marker after it would push the total past _CONTENT_CHAR_CAP —
    # exactly the bug this whole fix exists to close, just moved one line
    # over. `limit` is where the KEPT text must end so text + marker still
    # fits inside the cap.
    limit = max(0, _CONTENT_CHAR_CAP - len(marker))
    cut = doc_support.rfind_safe_cut(text, limit)
    return text[:cut] + marker


def _split_long_lines(md: str, limit: int) -> str:
    """Break any single line longer than `limit` characters into limit-sized
    chunks, each its own line. read_cap.window pages by LINE with no
    character budget of its own (that's read.py's contract, owned
    elsewhere) — a document that is one enormous unbroken line (an 800k-
    character pasted transcript, verified live) is, from window()'s point of
    view, exactly ONE line, so no amount of line-based paging can ever
    return less than the whole thing. Pre-splitting on this side turns that
    one line into many synthetic ones window() can page through normally,
    without read.py ever needing a character budget of its own. Purely a
    windowing aid — only ever fed to read_cap.window(), never used for
    outline/section, which must keep seeing the document's real structure.
    """
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: List[str] = []
    for line in lines:
        if len(line) <= limit:
            out.append(line)
        else:
            out.extend(line[i:i + limit] for i in range(0, len(line), limit))
    return "\n".join(out)


def _char_bounded_window(md: str, offset: int) -> Tuple[str, Optional[int]]:
    """read_cap.window(), trimmed to _CONTENT_CHAR_CAP: ask it for its
    normal line-based window, then keep only as many of those lines as fit
    the character cap, recomputing `next_offset` from what was ACTUALLY
    kept rather than trusting window()'s own next_offset (which assumes
    every line it returned is being kept). Getting this recomputation
    right is the whole point — if a caller advances by window()'s original
    next_offset after we silently dropped some of its lines, it skips the
    content that got trimmed. The first line is always kept even if it
    alone exceeds the cap (hard-cut to size), so a call always makes
    forward progress and never returns an empty page while lines remain.
    """
    win = read_cap.window(_split_long_lines(md, _CONTENT_CHAR_CAP), offset)
    lines = win["lines"]
    kept: List[str] = []
    used = 0
    for line in lines:
        cost = len(line) + (1 if kept else 0)
        if kept and used + cost > _CONTENT_CHAR_CAP:
            break
        kept.append(line)
        used += cost
    if not kept and lines:
        kept = [lines[0][:_CONTENT_CHAR_CAP]]
    next_offset = (offset + len(kept)) if len(kept) < len(lines) else win["next_offset"]
    return "\n".join(kept), next_offset


@mcp.tool()
@mcp_envelope
def es_read(target: str, section: Optional[str] = None,
            query: Optional[str] = None, offset: Optional[int] = None) -> dict:
    """Read a vault note or a document previously extracted by es_doc_extract,
    paged by heading so a long one doesn't have to come back all at once.

    target is a vault-relative path, a topic name (same convention as
    es_notes_journal/es_notes_topic/es_notes_attach), or a "doc:<id>" handle
    returned by es_doc_extract.

    No arguments: a short note or document comes back WHOLE (more=false). A
    long one instead comes back as `outline` — a list of {id, title, level}
    in document order — with a short preview in `content` and more=true; pass
    an outline id as `section` to read that piece in full (its own
    subsections included). "Long" is a higher bar for a vault note (authored
    by the user, naturally bounded) than for a document es_doc_extract
    converted (arrives at whatever size its source happened to be) — an
    ordinary journal/topic note almost always comes back whole. query
    full-text searches headings + bodies, case-insensitively, in `outline`
    — the shape depends on whether the document has headings at all: WITH
    headings, matches come back as outline entries ({id, title, level},
    including a preamble hit if the text before the first heading matches)
    to follow up with `section`; a document with NO headings (a .csv, a
    flat prose note) instead returns line hits ({offset, line}) to follow
    up with `offset=` — there is no section id to hand back for flat
    content, and returning the whole thing just because a search term
    matched somewhere inside it would defeat offset-paging's whole point.
    Either way `content` stays null (the hits are what to act on); no
    matches still returns ok, with `content` naming what to try instead.
    offset pages by LINE, for content with no headings at all (e.g. a
    .csv) or to page raw text regardless of headings, and reports
    `next_offset` (null once nothing is left); ignored when section is
    also given — section wins. `content` is capped per call — when cut,
    it ends with an in-band marker naming how to get the rest (typically
    `offset=0`).

    Always returns {kind, source, path, frontmatter, content, outline, more,
    next_offset} — the same keys regardless of mode, so you never have to
    guess which ones exist. path/frontmatter are null for a doc:<id> target;
    outline is null except where noted above.
    """
    # _notes_client() reads config.yaml — skip it for a doc: target, which
    # never touches the vault, so a bare "doc:<id>" read doesn't require a
    # configured vault at all.
    vault = None if target.startswith(reader.DOC_PREFIX) else _notes_client()
    resolved = reader.resolve(target, vault=vault, cache_root=_doc_cache_root())
    md = resolved["markdown"]
    out = {
        "kind": resolved["kind"],
        "source": resolved["source"],
        "path": resolved.get("path"),
        "frontmatter": resolved.get("frontmatter"),
        "content": None,
        "outline": None,
        "more": False,
        "next_offset": None,
    }

    if query is not None:
        hits = read_cap.query(md, query)
        out["outline"] = hits
        if not hits:
            out["content"] = (f"No section matched {query!r}. Call es_read with no "
                              "arguments to see the outline, or try a different word.")
        return out

    _resume_hint = ("this is a single section with no paging of its own — "
                     "call es_read with offset=0 to page through the whole "
                     "document by line instead")

    if section is not None:
        out["content"] = _cap_content(read_cap.section(md, section), _resume_hint)
        out["more"] = len(read_cap.outline(md)) > 1
        return out

    if offset is not None:
        out["content"], out["next_offset"] = _char_bounded_window(md, offset)
        out["more"] = out["next_offset"] is not None
        return out

    outline = read_cap.outline(md)
    whole_limit = (_WHOLE_NOTE_CHAR_LIMIT if resolved["kind"] == "note"
                   else _WHOLE_DOCUMENT_CHAR_LIMIT)
    large = len(md) > whole_limit
    if outline and large:
        out["outline"] = outline
        # The spec calls this "an outline plus the first section" — the
        # preamble (text before the first heading) IS that first section
        # when one exists, but a document that starts AT a heading (no
        # preamble text at all, verified live for a PDF and an .xlsx) has
        # none, and returning null here regressed the spec's promise and
        # left the agent with an outline and nothing to read without a
        # second call. Falling back to the first heading's own body means
        # `content` is only ever null when the document is genuinely empty.
        preview = read_cap.section(md, read_cap.PREAMBLE_ID).strip()
        if not preview:
            preview = read_cap.section(md, outline[0]["id"]).strip()
        out["content"] = _cap_content(preview, _resume_hint) or None
        out["more"] = True
        return out

    if large:  # no headings to outline (e.g. a plain .txt) — page by line
        out["content"], out["next_offset"] = _char_bounded_window(md, 0)
        out["more"] = out["next_offset"] is not None
        return out

    out["content"] = md
    out["outline"] = outline or None
    return out


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
def es_maps_geocode(query: str, include_timezone: bool = False) -> dict:
    """Geocode an address/place text to {address, lat, lng, place_id}. Building block; returns
    null-ish if nothing matches. Needs maps.api_key in config.

    include_timezone=true adds the location's IANA `timezone` — use it when creating a calendar
    event somewhere you're unsure of the zone, then pass that as es_cal_add's `tz`."""
    return maps_cap.geocode(query, include_timezone=include_timezone)


@mcp.tool()
@mcp_envelope
def es_time(timezone: Optional[str] = None) -> Envelope[clock_cap.CurrentTime]:
    """The current date and time. CALL THIS FIRST for anything relative — "today", "tonight",
    "this weekend", "tomorrow", "in two hours" — and before writing any dated event.

    Do NOT infer the date from the system prompt. That line is labelled `Conversation started:`
    and is exactly that: the date this conversation BEGAN, which may be weeks ago. It is also
    date-only by design. This tool is the only authoritative source of now.

    Returns {iso, date, time, weekday, timezone, utc_offset, utc} in the operator's configured
    timezone; pass `timezone` (IANA) for another location's local time."""
    return clock_cap.now(timezone)


@mcp.tool()
@mcp_envelope
def es_weather(location: str, start: Optional[str] = None,
               end: Optional[str] = None) -> Envelope[weather_cap.WeatherReport]:
    """Weather for a location. `location` is any place text (geocoded internally) and is REQUIRED.

    No start/end means right now. Otherwise both are wall-clock times AT THE LOCATION —
    "2026-08-15T09:00" (naive, resolved in the location's timezone), or "2026-08-15" for a
    whole local day. Ranges are inclusive of the end date, so start=Sat end=Sun is the weekend.

    Returns periods[] — a window up to 24h yields one period per hour; longer windows merge
    adjacent similar hours (splitting where weather turns), so `start`/`end` tell you how much
    precision you actually have. Forecasts run 10 days out.

    IMPORTANT: `condition` describes the sky and can read "Sunny" on an hour with a 70%
    `thunderstorm_prob` — Google models them independently. For outdoor activities
    `thunderstorm_prob` is authoritative for lightning risk, never `condition` alone.
    """
    api_key = maps_cap.api_key()
    unit_system = weather_cap.units()
    geo = maps_cap.geocode(location)
    if not geo or geo.get("lat") is None:
        raise weather_cap.WeatherError("weather_location_not_found",
                                       f"Could not find a location for {location!r}.")

    now = datetime.now(timezone.utc)
    # Page 1 is fetched before the window can be resolved: the location's zone is only known
    # from the response, and it is needed to turn a naive local time into an absolute one.
    probe, tzname = weather_cap.fetch_hours(geo["lat"], geo["lng"], 1, api_key, unit_system)
    if not probe:
        raise weather_cap.WeatherError("weather_error", "No forecast returned for that location.")

    if start is None:
        hours, window = probe, 1.0
    else:
        s = weather_cap.parse_input_time(start, tzname)
        e = (weather_cap.parse_input_time(end, tzname, end_of_day=True)
             if end else s + timedelta(hours=1))
        want = weather_cap.hours_needed(e, now)
        fetched, _ = weather_cap.fetch_hours(geo["lat"], geo["lng"], want, api_key, unit_system)
        hours = [h for h in fetched
                 if datetime.fromisoformat(h.interval.endTime.replace("Z", "+00:00")) > s
                 and datetime.fromisoformat(h.interval.startTime.replace("Z", "+00:00")) < e]
        if not hours:
            raise weather_cap.WeatherError("weather_no_hours",
                                           "No forecast hours fall inside that window.")
        window = (e - s).total_seconds() / 3600.0

    return weather_cap.WeatherReport(
        location=weather_cap.Location(address=geo["address"], lat=geo["lat"],
                                      lng=geo["lng"], timezone=tzname),
        units=unit_system,
        periods=weather_cap.build_periods(hours, tzname, window),
    )


@mcp.tool()
@mcp_envelope
def es_maps_star(place_id: str, list: Optional[str] = None) -> dict:
    """Save (star) a place to a Google Maps list — this is what makes it appear in Android Auto /
    Google Automotive.

    `place_id` is REQUIRED and exact. Get it from es_maps_search for a BUSINESS, or
    es_maps_geocode for a STREET ADDRESS — they return different ids for the same spot, and
    starring the geocoded one saves the address ("11521 N FM 620") rather than the place
    ("Torchy's Tacos"). For a named business, always use es_maps_search.

    `list` defaults to maps.save_list in config ("Starred", matching Google's "Starred places").
    Idempotent: already-saved returns changed=false. Drives the logged-in browser, so it can
    raise authentication_required — run es_login then retry. On maps_automation_stale, fall back
    to sending the Maps deep link."""
    return maps_write.set_saved(place_id, list or maps_write.save_list_default(),
                                want_saved=True, **maps_write.live_driver())


@mcp.tool()
@mcp_envelope
def es_maps_unstar(place_id: str, list: Optional[str] = None) -> dict:
    """Remove a place from a Google Maps saved list. `place_id` is REQUIRED and exact; idempotent.

    Use the SAME place_id that was starred. Resolving free text here would search all of Google
    Maps rather than your saved places, so "unstar Torchy's" could silently resolve to a different
    branch and report changed=false while the one you meant stays saved."""
    return maps_write.set_saved(place_id, list or maps_write.save_list_default(),
                                want_saved=False, **maps_write.live_driver())


@mcp.tool()
@mcp_envelope
def es_maps_lists() -> list:
    """The account's Google Maps saved lists with place counts: [{list, count}].

    Use this to discover exact list names — es_maps_star/unstar match them EXACTLY."""
    d = maps_write.live_driver()
    return maps_write.all_lists(**{k: v for k, v in d.items() if k != "persist"})


@mcp.tool()
@mcp_envelope
def es_maps_list_places(list: str) -> list:
    """Clean place NAMES in a saved list, in display order: ["Torchy's Tacos", "Uchi"].

    Names only — Google's saved-list view carries no place ids. Duplicates are returned as
    duplicates (two starred branches of a chain look identical here). Turn a name into a
    place_id with es_maps_resolve; act with es_maps_star/unstar."""
    d = maps_write.live_driver()
    return maps_write.list_places(list, **{k: v for k, v in d.items() if k != "persist"})


@mcp.tool()
@mcp_envelope
def es_maps_place_lists(place_id: str) -> list:
    """Which of the account's lists contain THIS place: [{list, in_list}].

    The only EXACT way to ask whether a place is saved — es_maps_list_places answers by name,
    which cannot distinguish two branches of a chain. Do not test with es_maps_star: calling it
    on an unsaved place SAVES it."""
    d = maps_write.live_driver()
    return maps_write.read_lists(place_id, **{k: v for k, v in d.items() if k != "persist"})


@mcp.tool()
@mcp_envelope
def es_maps_resolve(list: str, name: str) -> list:
    """A place name in a list -> [{place_id, address}]. ALWAYS an array.

    Slower than the other reads (it opens the place to identify it). A name is not a key, so a
    chain with two starred branches returns two entries — pick using the addresses. Feed the
    place_id to es_maps_unstar."""
    d = maps_write.live_driver()
    return maps_write.resolve_name(list, name, search=maps_cap.search,
                                   **{k: v for k, v in d.items() if k != "persist"})


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
def es_login(profile: str = "google") -> dict:
    """Prepare or confirm an interactive web login for an authenticated browser profile. The
    profile is the AUTH TARGET (e.g. "google"), shared by every consumer of that account
    (es_maps_* etc.) — not the capability. Idempotent: probes liveness via a live google.com
    browse; if signed in, closes the login window and returns {status:"logged_in"}; if not,
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
