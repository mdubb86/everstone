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
from es import config

_PROFILE = "google"
_PLACE_URL = "https://www.google.com/maps/place/?q=place_id:{pid}"
_SAVE_BTN = "Save"
_SAVED_BTN = "Saved"
_ITEM_SELECTOR = "[role=menuitemradio]"
_SAVE_SELECTOR = 'button[aria-label="Save"],button[aria-label="Saved"]'
_DEFAULT_LIST = "Starred"          # prefix-matches Google's "Starred places"


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
    """Exact match first, then case-insensitive prefix — so the shipped default
    `Starred` selects Google's "Starred places" without hardcoding their wording.
    """
    t = (target or "").strip().lower()
    for it in items:
        if it["name"].lower() == t:
            return it
    hits = [it for it in items if it["name"].lower().startswith(t)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise MapsAutomationError(
            "maps_list_ambiguous",
            f"{target!r} matches several lists: {', '.join(h['name'] for h in hits)}.")
    raise MapsAutomationError(
        "maps_list_not_found",
        f"No saved list matching {target!r}. Available: {', '.join(i['name'] for i in items)}.")


def save_list_default() -> str:
    return (config.maps_config() or {}).get("save_list") or _DEFAULT_LIST


# ── JS payloads (the fragile part, kept together) ──────────────────────────

_JS_BUTTON_LABEL = """(() => {
  const b = [...document.querySelectorAll('button,[role=button]')]
    .find(x => ['Save','Saved'].includes((x.getAttribute('aria-label')||'').trim()));
  return b ? b.getAttribute('aria-label').trim() : null;
})()"""

_JS_CLICK_SAVE = """(() => {
  const b = [...document.querySelectorAll('button,[role=button]')]
    .find(x => ['Save','Saved'].includes((x.getAttribute('aria-label')||'').trim()));
  if (!b) return false;
  b.click(); return true;
})()"""

_JS_READ_ITEMS = """(() => [...document.querySelectorAll('%s')].map(x => ({
  text: (x.textContent||'').trim(),
  checked: x.getAttribute('aria-checked')
})))()""" % _ITEM_SELECTOR

_JS_CLICK_ITEM = """(() => {
  const it = [...document.querySelectorAll('%s')];
  if (!it[%%d]) return false;
  it[%%d].click(); return true;
})()""" % _ITEM_SELECTOR

_JS_COUNT = "(() => document.querySelectorAll(%r).length)()"

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


def _open_picker(tab_id, *, evaluate, sleep, probe_signed_in=None):
    if not await_selector(tab_id, _SAVE_SELECTOR, evaluate=evaluate, sleep=sleep):
        _raise_stale_or_auth(
            "Could not find the Save button on the Maps place page — the UI has likely changed.",
            probe_signed_in)
    if not evaluate(tab_id, _JS_CLICK_SAVE):
        _raise_stale_or_auth(
            "The Save button vanished before it could be clicked.", probe_signed_in)
    if not await_selector(tab_id, _ITEM_SELECTOR, evaluate=evaluate, sleep=sleep):
        _raise_stale_or_auth(
            "The save-list picker did not render any lists — the UI has likely changed.",
            probe_signed_in)
    return parse_items(evaluate(tab_id, _JS_READ_ITEMS))


def set_saved(place_id, target_list, *, want_saved, navigate, evaluate, sleep,
              probe_signed_in, persist, attempts=3):
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
                                   probe_signed_in=probe_signed_in, persist=persist)
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
                    probe_signed_in, persist):
    tab_id = navigate(_PLACE_URL.format(pid=place_id))
    items = _open_picker(tab_id, evaluate=evaluate, sleep=sleep,
                         probe_signed_in=probe_signed_in)
    target = choose_list(items, target_list)

    if target["checked"] == want_saved:
        evaluate(tab_id, _JS_ESCAPE)
        return {"place_id": place_id, "list": target["name"],
                "saved": want_saved, "changed": False}

    # Indices are only valid for the read that produced them — the picker
    # reorders, floating the checked list to position 0 — so this must click
    # within the same picker session that was just read.
    if not evaluate(tab_id, _JS_CLICK_ITEM % (target["index"], target["index"])):
        raise MapsAutomationError(
            "maps_automation_stale",
            f"Could not click the {target['name']!r} list entry — the UI has likely changed.")

    # Verify against the DOM rather than trusting the click: a silent no-op here
    # would report success while nothing was saved.
    wanted_label = _SAVED_BTN if want_saved else _SAVE_BTN
    for _ in range(10):
        if evaluate(tab_id, _JS_BUTTON_LABEL) == wanted_label:
            evaluate(tab_id, _JS_ESCAPE)   # leave no dialog open for the next call
            persist()
            return {"place_id": place_id, "list": target["name"],
                    "saved": want_saved, "changed": True}
        sleep(1.0)
    raise MapsAutomationError(
        "maps_automation_stale",
        f"Clicked {target['name']!r} but the button never became {wanted_label!r} — "
        "could not confirm the write.")


def read_lists(place_id, *, navigate, evaluate, sleep, probe_signed_in, attempts=3):
    """The account's Google Maps save lists, and whether this place is in each."""
    last = None
    for attempt in range(attempts):
        try:
            tab_id = navigate(_PLACE_URL.format(pid=place_id))
            items = _open_picker(tab_id, evaluate=evaluate, sleep=sleep,
                                 probe_signed_in=probe_signed_in)
            evaluate(tab_id, _JS_ESCAPE)
            return [{"name": i["name"], "saved": i["checked"]} for i in items]
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

    def evaluate(tab_id, expression):
        r = httpx.post(f"{base}/tabs/{tab_id}/evaluate",
                       json={"userId": _PROFILE, "expression": expression}, timeout=45)
        r.raise_for_status()
        return (r.json() or {}).get("result")

    return {
        "navigate": navigate,
        "evaluate": evaluate,
        "sleep": time.sleep,
        "probe_signed_in": lambda: bool(wl.signed_in_from_home(wl.probe_home(_PROFILE))),
        "persist": lambda: wl.fetch_state(_PROFILE),
    }
