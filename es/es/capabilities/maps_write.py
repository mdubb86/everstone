"""Maps writes (M2) — drive Saved Places in the authenticated `google` browser.

No Google API writes Saved Places, so this automates the Maps web UI in the
logged-in camofox-auth profile. Starring is what surfaces a place in Google
Automotive / Android Auto, which is the point of the feature.

ALL selector logic lives in this module. When Google reshuffles the UI, this is
the only file to fix, and every failure path raises `maps_automation_stale` so
the agent can fall back to a deep link rather than silently doing nothing.

Structure follows es.web_login: browser primitives are injected, so the flow
logic is unit-testable without a browser.

DOM contract (verified live 2026-08-14):
  - the place page has a button with aria-label exactly "Save" (or "Saved" once
    the place is in any list)
  - the picker renders one [role=menuitemradio] per list, carrying aria-checked
  - each item's textContent is "<name>Private · N places" — the list name is
    everything before "Private"
"""
import json

from es import config

_PROFILE = "google"
_PLACE_URL = "https://www.google.com/maps/place/?q=place_id:{pid}"
# The saved-lists panel. Reached in the UI by the "Saved" rail item; the data=
# suffix is what that click produces. Coordinates are irrelevant (the panel is
# account-scoped) but the URL requires some centre.
_SAVED_URL = "https://www.google.com/maps/@30.45,-97.82,12z/data=!4m2!10m1!1e1"
_SAVE_BTN = "Save"
_SAVED_BTN = "Saved"
_ITEM_SELECTOR = "[role=menuitemradio]"
# Google rewrites this label by list count: "Save" -> "Saved" -> "Saved (2)".
# Matching the first two exactly meant a place in 2+ lists became invisible and
# EVERY operation failed with save=0. Prefix-match instead.
_SAVE_SELECTOR = 'button[aria-label^="Save"]'
_DEFAULT_LIST = "Starred places"   # Google's actual list name — see choose_list


class MapsAutomationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.es_code = code


# ── pure helpers (unit-tested; no browser) ─────────────────────────────────

def list_name(text: str) -> str:
    """"Starred placesPrivate" -> "Starred places".

    Two things to strip, both found only by reading the live DOM:

    1. Material icon glyphs in the PRIVATE USE AREA (U+E000–U+F8FF) prefix the
       name — textContent picks up the <i> icon element. Left in place they
       silently break prefix matching, because "starred places" does not
       start with "starred".
    2. The "Private · N places" subtitle is concatenated with no separator.

    Splitting on "Private" is English-only; a localised UI would need that
    locale's word, which is why a miss raises rather than guessing.
    """
    cleaned = "".join(c for c in (text or "") if not ("" <= c <= ""))
    return cleaned.split("Private")[0].strip()


def parse_items(raw) -> list:
    """[{text, checked}] from the DOM -> [{name, checked, index}]."""
    out = []
    for i, it in enumerate(raw or []):
        name = list_name(it.get("text", ""))
        if name:
            out.append({"name": name, "checked": it.get("checked") == "true", "index": i})
    return out


def choose_list(items: list, target: str) -> dict:
    """EXACT match, case-insensitive. No prefix matching.

    Prefix matching existed only to paper over a shipped default of "Starred"
    when Google's list is "Starred places" — a self-inflicted problem that
    dragged in an ambiguity error ("Sa" matching both "Saved places" and
    "Starred places"). Lists are a closed, enumerable set; es_maps_lists returns
    them. Fixing the default removes the need to guess.
    """
    t = (target or "").strip().lower()
    for it in items:
        if it["name"].lower() == t:
            return it
    raise MapsAutomationError(
        "maps_list_not_found",
        f"No saved list named {target!r}. Available: {', '.join(i['name'] for i in items)}.")


def save_list_default() -> str:
    return (config.maps_config() or {}).get("save_list") or _DEFAULT_LIST


# ── JS payloads (the fragile part, kept together) ──────────────────────────

_JS_BUTTON_LABEL = """(() => {
  const b = [...document.querySelectorAll('button,[role=button]')]
    .find(x => /^Save/.test((x.getAttribute('aria-label')||'').trim()));
  return b ? b.getAttribute('aria-label').trim() : null;
})()"""

_JS_CLICK_SAVE = """(() => {
  const b = [...document.querySelectorAll('button,[role=button]')]
    .find(x => /^Save/.test((x.getAttribute('aria-label')||'').trim()));
  if (!b) return false;
  b.click(); return true;
})()"""

_JS_READ_ITEMS = """(() => [...document.querySelectorAll('%s')].map(x => ({
  text: (x.textContent||'').trim(),
  checked: x.getAttribute('aria-checked')
})))()""" % _ITEM_SELECTOR

# Find AND click by NAME in one evaluate. Indices are unusable: the picker
# reorders, floating checked lists to the top, so an index read in one call can
# point at a different row by the next — which is how a "star" landed in
# Favorites and corrupted the state this was trying to read.
_JS_CLICK_ITEM = """(() => {
  const clean = t => (t||'').replace(/[\ue000-\uf8ff]/g, '').split('Private')[0].trim();
  const el = [...document.querySelectorAll('%s')].find(x => clean(x.textContent) === %%s);
  if (!el) return false;
  el.click(); return true;
})()""" % _ITEM_SELECTOR

_JS_COUNT = "(() => document.querySelectorAll(%r).length)()"

# Saved-list entries carry NO identifier — no href, no data-*, no ChIJ, no hex
# feature id, and the jslog payload is a search-context token. Verified by
# sweeping self + 3 ancestors + every descendant attribute. So the panel yields
# names only; ids require clicking through (see resolve_name).
_JS_LIST_ENTRIES = r"""(() => {
  const clean = t => (t||'').replace(/[-]/g, '').split('Private')[0].trim();
  return [...document.querySelectorAll('button,[role=button],a')]
    .map(x => ({ full: (x.textContent||'').trim(), name: clean(x.textContent) }))
    .filter(o => o.name && /Private/.test(o.full))
    .map(o => ({ name: o.name, count: (o.full.match(/(\d+)\s*place/)||[])[1] || "0" }));
})()"""

_JS_OPEN_LIST = r"""(() => {
  const clean = t => (t||'').replace(/[-]/g, '').split('Private')[0].trim();
  const el = [...document.querySelectorAll('button,[role=button],a')]
    .find(x => clean(x.textContent) === %s);
  if (!el) return false;
  el.click(); return true;
})()"""

# Places inside an opened list. The row element concatenates name + rating +
# reviews + price ("Torchy's Tacos4.4(2,792)$10-20"); a CHILD div holds the
# clean name, so take the SHORTEST descendant text that the row's text starts
# with. Pinning Google's generated class (fontHeadlineSm) would be brittle.
_JS_LIST_PLACES = r"""(() => {
  const out = [];
  for (const el of document.querySelectorAll('button,[role=button]')) {
    // Results-pane rows carry jsaction="pane.*". The navigation rail uses
    // "click:navigationrail.*" and otherwise looks identical to this heuristic,
    // which is how the "10Austin & Cedar Park" chip leaked in as a place named
    // "10".
    if (!/^pane\./.test(el.getAttribute('jsaction')||'')) continue;
    const full = (el.textContent||'').trim();
    if (!full || full.length > 140 || /Private/.test(full)) continue;
    let best = null;
    for (const k of el.querySelectorAll('div')) {
      const t = (k.textContent||'').trim();
      if (t && t.length > 1 && full.startsWith(t) && t.length < full.length
          && (!best || t.length < best.length)) best = t;
    }
    if (best) out.push(best);
  }
  return out;
})()"""

# Clicks the Nth row matching `want`. The index addresses DUPLICATES — two
# starred branches of a chain are identical in this view — and is safe because
# it is consumed within the single read that produced it, never stored.
_JS_CLICK_PLACE = r"""(() => {
  const want = %s, wantIdx = %d;
  let n = 0;
  for (const el of document.querySelectorAll('button,[role=button]')) {
    if (!/^pane\./.test(el.getAttribute('jsaction')||'')) continue;
    const full = (el.textContent||'').trim();
    if (full.startsWith(want) && !/Private/.test(full)) {
      if (n === wantIdx) { el.click(); return true; }
      n++;
    }
  }
  return false;
})()"""

_JS_ADDRESS = r"""(() => {
  const b = [...document.querySelectorAll('button,[role=button]')]
    .find(x => /^Address:/.test(x.getAttribute('aria-label')||''));
  return b ? b.getAttribute('aria-label').replace(/^Address:\s*/, '') : null;
})()"""

_JS_ESCAPE = """(() => {
  document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
  return true;
})()"""


# ── flow ───────────────────────────────────────────────────────────────────

def _raise_stale_or_auth(message, probe_signed_in):
    """A signed-out session and a reshuffled UI look identical from the DOM — no
    Save button either way — but they need different responses: es_login retry
    vs deep-link fallback. Probe only here, on the failure path, because
    probe_signed_in navigates the same profile and racing it against the place
    page made the button intermittently unfindable."""
    if probe_signed_in and not probe_signed_in():
        raise MapsAutomationError(
            "authentication_required",
            "The Google browser session is signed out. Run es_login to restore it.")
    raise MapsAutomationError("maps_automation_stale", message)


def await_selector(tab_id, selector, *, evaluate, sleep, timeout_s=25.0, interval=1.0):
    """Wait for `selector` by POLLING evaluate — never camofox's /tabs/{id}/wait.

    That endpoint reports success ({"ok": true, "ready": true}) and leaves the
    DOM byte-identical, but any synthetic .click() afterwards silently fails to
    trigger its handler. Measured, alternating, on the same page:

        wait=True   clicked=True  radios=0      wait=True   clicked=True  radios=0
        wait=False  clicked=True  radios=6      wait=False  clicked=True  radios=6

    Polling evaluate gives a real selector-await with none of that, and is why
    this flow has no blind sleeps.
    """
    waited = 0.0
    while True:
        if evaluate(tab_id, _JS_COUNT % selector):
            return True
        if waited >= timeout_s:
            return False
        sleep(interval)
        waited += interval


# Google Maps needs a beat AFTER the Save button appears before it will act on a
# click. Measured: the button is byte-identical from t+1s (same attrs, same
# jsaction, stable bounding box, not disabled) but clicks land dead until
# ~3.5s after navigation. Awaiting the selector alone returns at ~1s and clicks
# into that window:
#
#   poll -> click at 3.1-3.4s   radios=0   (dead)
#   sleep -> click at 3.8-3.9s  radios=6   (works)
#
# There is nothing in the DOM to await for this, so it is a bounded settle after
# the selector gate — not instead of it.
_POST_LOAD_SETTLE_S = 2.5


def _open_picker(tab_id, *, evaluate, sleep, probe_signed_in=None):
    if not await_selector(tab_id, _SAVE_SELECTOR, evaluate=evaluate, sleep=sleep):
        _raise_stale_or_auth(
            "Could not find the Save button on the Maps place page — the UI has likely changed.",
            probe_signed_in)
    sleep(_POST_LOAD_SETTLE_S)
    if not evaluate(tab_id, _JS_CLICK_SAVE):
        _raise_stale_or_auth(
            "The Save button vanished before it could be clicked.", probe_signed_in)
    if not await_selector(tab_id, _ITEM_SELECTOR, evaluate=evaluate, sleep=sleep):
        _raise_stale_or_auth(
            "The save-list picker did not render any lists — the UI has likely changed.",
            probe_signed_in)
    return parse_items(evaluate(tab_id, _JS_READ_ITEMS))


def set_saved(place_id, target_list, *, want_saved, navigate, evaluate, sleep,
              probe_signed_in, persist, close_tab=None, attempts=3):
    """Star (want_saved=True) or unstar (False) `place_id` in `target_list`.

    Idempotent: if the list is already in the wanted state nothing is clicked and
    `changed` is False — which is what makes the retry below safe.

    RETRIES THE WHOLE OPERATION on a fresh page. Driving a heavy SPA fails
    intermittently for reasons outside our control: an await can time out because
    the picker never rendered, and camofox itself sometimes blocks past the HTTP
    timeout. Both are "the browser was busy", both clear on a fresh navigation,
    and neither can double-apply because the first thing a retry does is re-read
    the current state. Retrying inside the flow (rather than leaving it to the
    agent) also avoids burning a full 25s await timeout on the agent's clock.
    """
    last = None
    for attempt in range(attempts):
        try:
            return _set_saved_once(place_id, target_list, want_saved=want_saved,
                                   navigate=navigate, evaluate=evaluate, sleep=sleep,
                                   probe_signed_in=probe_signed_in, persist=persist,
                                   close_tab=close_tab)
        except MapsAutomationError as e:
            if e.es_code == "authentication_required":
                raise                      # a retry cannot fix a signed-out session
            last = e
        except Exception as e:             # noqa: BLE001
            # httpx.ReadTimeout and friends: camofox intermittently blocks past
            # the HTTP timeout. Catching only MapsAutomationError let these kill
            # the call unretried, which is how a transient browser stall reached
            # the agent as an untyped failure with no es_code at all.
            last = MapsAutomationError(
                "maps_automation_stale",
                f"Browser automation failed ({type(e).__name__}): {str(e)[:120]}")
        if attempt + 1 < attempts:
            sleep(2.0)
    raise last


def _set_saved_once(place_id, target_list, *, want_saved, navigate, evaluate, sleep,
                    probe_signed_in, persist, close_tab=None):
    tab_id = navigate(_PLACE_URL.format(pid=place_id))
    try:
        items = _open_picker(tab_id, evaluate=evaluate, sleep=sleep,
                             probe_signed_in=probe_signed_in)
        target = choose_list(items, target_list)

        if target["checked"] == want_saved:
            evaluate(tab_id, _JS_ESCAPE)
            # Payload carries only what the caller could NOT predict. place_id
            # and the requested state are echoes of the arguments; the resolved
            # list name stopped being information once matching became exact.
            return {"changed": False}

        # Indices are only valid for the read that produced them — the picker
        # reorders, floating the checked list to position 0 — so this must click
        # within the same picker session that was just read.
        if not evaluate(tab_id, _JS_CLICK_ITEM % json.dumps(target["name"])):
            raise MapsAutomationError(
                "maps_automation_stale",
                f"Could not click the {target['name']!r} list entry — the UI has likely changed.")

        # Verify against the DOM rather than trusting the click: a silent no-op here
        # would report success while nothing was saved.
        # "Saved", "Saved (2)", ... all mean saved; only bare "Save" means not.
        for _ in range(10):
            lbl = evaluate(tab_id, _JS_BUTTON_LABEL) or ""
            if lbl.startswith(_SAVED_BTN) == bool(want_saved):
                evaluate(tab_id, _JS_ESCAPE)   # leave no dialog open for the next call
                persist()
                return {"changed": True}
            sleep(1.0)
        raise MapsAutomationError(
            "maps_automation_stale",
            f"Clicked {target['name']!r} but the button never became {wanted_label!r} — "
            "could not confirm the write.")
    finally:
        if close_tab:
            close_tab(tab_id)


def read_lists(place_id, *, navigate, evaluate, sleep, probe_signed_in, close_tab=None, attempts=3):
    """The account's Google Maps save lists, and whether this place is in each."""
    last = None
    for attempt in range(attempts):
        try:
            tab_id = navigate(_PLACE_URL.format(pid=place_id))
            try:
                items = _open_picker(tab_id, evaluate=evaluate, sleep=sleep,
                                     probe_signed_in=probe_signed_in)
                evaluate(tab_id, _JS_ESCAPE)
                return [{"list": i["name"], "in_list": i["checked"]} for i in items]
            finally:
                if close_tab:
                    close_tab(tab_id)
        except MapsAutomationError as e:
            if e.es_code == "authentication_required":
                raise                      # a retry cannot fix a signed-out session
            last = e
        except Exception as e:             # noqa: BLE001
            # httpx.ReadTimeout and friends: camofox intermittently blocks past
            # the HTTP timeout. Catching only MapsAutomationError let these kill
            # the call unretried, which is how a transient browser stall reached
            # the agent as an untyped failure with no es_code at all.
            last = MapsAutomationError(
                "maps_automation_stale",
                f"Browser automation failed ({type(e).__name__}): {str(e)[:120]}")
        if attempt + 1 < attempts:
            sleep(2.0)
    raise last


def all_lists(*, navigate, evaluate, sleep, probe_signed_in, close_tab=None):
    """The account's saved lists with place counts. No place required."""
    tab_id = navigate(_SAVED_URL)
    try:
        if not await_selector(tab_id, "button", evaluate=evaluate, sleep=sleep):
            _raise_stale_or_auth("The saved-lists panel did not render.", probe_signed_in)
        sleep(_POST_LOAD_SETTLE_S)
        rows = evaluate(tab_id, _JS_LIST_ENTRIES) or []
        if not rows:
            _raise_stale_or_auth("The saved-lists panel rendered no lists.", probe_signed_in)
        return [{"list": r["name"], "count": int(r["count"])} for r in rows]
    finally:
        if close_tab:
            close_tab(tab_id)


def _open_saved_list(tab_id, list_name, *, evaluate, sleep, probe_signed_in):
    if not await_selector(tab_id, "button", evaluate=evaluate, sleep=sleep):
        _raise_stale_or_auth("The saved-lists panel did not render.", probe_signed_in)
    sleep(_POST_LOAD_SETTLE_S)
    rows = evaluate(tab_id, _JS_LIST_ENTRIES) or []
    names = [r["name"] for r in rows]
    if list_name not in names:
        match = next((n for n in names if n.lower() == (list_name or "").strip().lower()), None)
        if not match:
            raise MapsAutomationError(
                "maps_list_not_found",
                f"No saved list named {list_name!r}. Available: {', '.join(names)}.")
        list_name = match
    if not evaluate(tab_id, _JS_OPEN_LIST % json.dumps(list_name)):
        _raise_stale_or_auth(f"Could not open the {list_name!r} list.", probe_signed_in)
    sleep(_POST_LOAD_SETTLE_S)
    return list_name


def list_places(list_name, *, navigate, evaluate, sleep, probe_signed_in, close_tab=None):
    """Clean place names in a list, in display order.

    NAMES ONLY — the entries carry no identifier of any kind (verified by
    sweeping every attribute on the row, its ancestors and its descendants).
    Duplicates are returned as duplicates rather than de-duplicated: two starred
    branches of the same chain are indistinguishable here, and hiding that would
    be worse than showing it. Use resolve_name to turn one into a place_id.
    """
    tab_id = navigate(_SAVED_URL)
    try:
        _open_saved_list(tab_id, list_name, evaluate=evaluate, sleep=sleep,
                         probe_signed_in=probe_signed_in)
        return evaluate(tab_id, _JS_LIST_PLACES) or []
    finally:
        if close_tab:
            close_tab(tab_id)


def resolve_name(list_name, name, *, navigate, evaluate, sleep, probe_signed_in,
                 search, close_tab=None):
    """A place name in a list -> [{place_id, address}]. ALWAYS an array.

    A name is not a key: two branches of a chain can both be starred and the
    list view cannot tell them apart. Returning an array with addresses lets the
    caller (or the operator) choose, instead of this guessing.

    The place page does NOT contain the ChIJ place_id — navigating by CID proves
    it (title and address correct, no ChIJ anywhere). So the id is recovered by
    searching Places with the page's own coordinates as bias, then VERIFYING the
    result's address matches the page's. Coordinate bias is what makes this safe:
    unbiased, "Torchy's Tacos" returns branches 200 miles away.
    """
    # Pass 1: count how many rows carry this name.
    tab_id = navigate(_SAVED_URL)
    try:
        _open_saved_list(tab_id, list_name, evaluate=evaluate, sleep=sleep,
                         probe_signed_in=probe_signed_in)
        names = evaluate(tab_id, _JS_LIST_PLACES) or []
    finally:
        if close_tab:
            close_tab(tab_id)
    n_matches = sum(1 for n in names if n == name)
    if not n_matches:
        raise MapsAutomationError(
            "maps_place_not_found",
            f"{name!r} is not in {list_name!r}. Present: {', '.join(names) or '(empty)'}.")

    # Pass 2: identify each match. Clicking navigates away from the list, so the
    # list is reopened per match rather than relying on back-navigation —
    # slower, but this is already the slow tool and predictability is worth more
    # than a few seconds.
    out = []
    for idx in range(n_matches):
        tab_id = navigate(_SAVED_URL)
        try:
            _open_saved_list(tab_id, list_name, evaluate=evaluate, sleep=sleep,
                             probe_signed_in=probe_signed_in)
            if not evaluate(tab_id, _JS_CLICK_PLACE % (json.dumps(name), idx)):
                _raise_stale_or_auth(
                    f"Could not open match {idx + 1} of {name!r}.", probe_signed_in)
            sleep(_POST_LOAD_SETTLE_S)
            url = evaluate(tab_id, "(() => location.href)()") or ""
            address = evaluate(tab_id, _JS_ADDRESS)
            out.append(_identify(name, url, address, search=search))
        finally:
            if close_tab:
                close_tab(tab_id)
    return out


def _identify(name, url, address, *, search):
    """Recover the real place_id for a row we clicked through to.

    Searches Places for "<name>, <address>" using the address shown on the place
    page, then VERIFIES the result's address matches it.

    NOT coordinate bias — an earlier version used the @lat,lng in the URL, which
    is the map VIEWPORT centre, not the place. With one pin it happened to sit on
    the place and looked correct; with two starred branches the viewport shifted
    and Places returned a third branch 15 miles away.

    NOT the address alone either — a bare address query returns the ADDRESS
    entity, a different place_id from the business at it (the same
    business-vs-address split as geocode vs search). Measured, for one branch:

        "Torchy's Tacos, 11521 Ranch Rd 620 N..." -> ChIJs0xt...  (the business)
        "11521 Ranch Rd 620 N..."                 -> ChIJUW5f...  (the address)
    """
    if not address:
        raise MapsAutomationError(
            "maps_automation_stale",
            f"No address on the place page for {name!r}; cannot identify it.")
    hits = search(f"{name}, {address}", limit=1) or []
    if not hits:
        raise MapsAutomationError(
            "maps_place_not_found", f"Places returned nothing for {name!r} at {address!r}.")
    best = hits[0]
    if best.get("address"):
        a = "".join(ch for ch in address.lower() if ch.isalnum())
        b = "".join(ch for ch in best["address"].lower() if ch.isalnum())
        if not (a.startswith(b[:18]) or b.startswith(a[:18])):
            raise MapsAutomationError(
                "maps_place_not_found",
                f"Could not confirm {name!r}: page says {address!r}, "
                f"Places says {best['address']!r}.")
    return {"place_id": best["place_id"], "address": best.get("address") or address}


def live_driver():
    """Bind the flow to the real camofox-auth instance.

    Has its own `evaluate` rather than reusing web_login._evaluate, which does
    `.get("result") or {}` — that turns a JS `false` into `{}`, losing exactly
    the signal the click helpers return.
    """
    import time
    import httpx
    import es.web_login as wl

    base = wl._CAMOFOX

    def navigate(url):
        r = httpx.post(f"{base}/tabs",
                       json={"userId": _PROFILE, "sessionKey": "default", "url": url}, timeout=60)
        r.raise_for_status()
        return r.json().get("tabId")

    def close_tab(tab_id):
        """Every navigate opens a tab and camofox never reaps an active one.

        Left unclosed they accumulate silently: GET /tabs reports [] without a
        userId, so the leak is invisible, while /pressure/cleanup showed 8 live
        tabs holding 2.0GB. That starved the VM (159MB free), and a memory-
        starved browser is exactly when pages load slowly and clicks land in
        dead windows — the whole intermittent-failure pattern. Closing them
        returned camoufox 2013MB -> 1092MB and the VM 4362MB -> 1959MB.
        """
        try:
            httpx.delete(f"{base}/tabs/{tab_id}", params={"userId": _PROFILE}, timeout=20)
        except Exception:  # noqa: BLE001 — best-effort cleanup must never mask the real result
            pass

    def evaluate(tab_id, expression):
        r = httpx.post(f"{base}/tabs/{tab_id}/evaluate",
                       json={"userId": _PROFILE, "expression": expression}, timeout=45)
        r.raise_for_status()
        return (r.json() or {}).get("result")

    return {
        "navigate": navigate,
        "evaluate": evaluate,
        "close_tab": close_tab,
        "sleep": time.sleep,
        "probe_signed_in": lambda: bool(wl.signed_in_from_home(wl.probe_home(_PROFILE))),
        "persist": lambda: wl.fetch_state(_PROFILE),
    }
