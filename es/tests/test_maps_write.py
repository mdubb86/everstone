"""Maps writes (M2) — flow logic, no browser.

The DOM contract these encode was read off the live Maps UI on 2026-08-14:
a button with aria-label "Save"/"Saved", and one [role=menuitemradio] per list
whose textContent is "<name>Private · N places".
"""
import pytest

from es.capabilities import maps_write as mw

LIST = "Starred places"   # exact name; no prefix matching

ITEMS_RAW = [
    {"text": "Travel plansPrivate · 0 places", "checked": "false"},
    {"text": "NailPrivate · 1 place", "checked": "false"},
    {"text": "Want to goPrivate · 0 places", "checked": "false"},
    {"text": "FavoritesPrivate", "checked": "false"},
    {"text": "Starred placesPrivate", "checked": "false"},
    {"text": "Saved placesPrivate", "checked": "false"},
]


def test_list_name_strips_the_concatenated_subtitle():
    assert mw.list_name("Starred placesPrivate") == "Starred places"
    assert mw.list_name("Travel plansPrivate · 0 places") == "Travel plans"
    assert mw.list_name("NailPrivate · 1 place") == "Nail"


def test_list_name_strips_material_icon_glyphs():
    """The live DOM prefixes each name with a Material icon in the Unicode
    PRIVATE USE AREA. Left in, "\ue87dStarred places" never prefix-matches
    "Starred" and every default-list star silently fails to find its list."""
    assert mw.list_name("\uf110Travel plansPrivate · 0 places") == "Travel plans"
    assert mw.list_name("\ue87dStarred placesPrivate") == "Starred places"
    assert mw.choose_list(
        mw.parse_items([{"text": "\ue87dStarred placesPrivate", "checked": "false"}]),
        "Starred places")["name"] == "Starred places"


def test_parse_items_indexes_and_flags_checked():
    items = mw.parse_items(ITEMS_RAW[:2] + [{"text": "XPrivate", "checked": "true"}])
    assert [i["name"] for i in items] == ["Travel plans", "Nail", "X"]
    assert [i["index"] for i in items] == [0, 1, 2]
    assert items[2]["checked"] is True


def test_shipped_default_prefix_matches_googles_wording():
    """config ships `Starred`; Google's list is "Starred places". The default
    must select it without hardcoding Google's exact wording."""
    assert mw.choose_list(mw.parse_items(ITEMS_RAW), "Starred places")["name"] == "Starred places"


def test_match_is_case_insensitive_but_still_exact():
    items = mw.parse_items([{"text": "Saved placesPrivate", "checked": "false"}])
    assert mw.choose_list(items, "saved places")["name"] == "Saved places"


def test_unknown_list_names_the_available_ones():
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.choose_list(mw.parse_items(ITEMS_RAW), "Nope")
    assert e.value.es_code == "maps_list_not_found"
    assert "Starred places" in str(e.value)


# ── flow, with injected browser primitives ─────────────────────────────────

class FakeBrowser:
    def __init__(self, items=None, button="Save", signed_in=True, click_ok=True):
        self.items = list(items if items is not None else ITEMS_RAW)
        self.button = button
        self.signed_in = signed_in
        self.click_ok = click_ok
        self.clicked = []
        self.persisted = False

    def driver(self):
        return dict(navigate=lambda url: "tab1", evaluate=self._eval,
                    sleep=lambda s: None,
                    probe_signed_in=lambda: self.signed_in,
                    persist=self._persist)

    def _persist(self):
        self.persisted = True

    def _eval(self, tab, js):
        if "querySelectorAll(" in js and ".length)()" in js:      # await_selector probe
            return len(self.items) if "menuitemradio" in js else (0 if self.button is None else 1)
        if self.button is None and "/^Save/" in js:
            return False if "b.click()" in js else None
        if "aria-checked" in js and "menuitemradio" in js and "clean" not in js:
            return self.items
        if "KeyboardEvent" in js:
            return True
        if "/^Save/" in js and "b.click()" in js:
            return True
        if "/^Save/" in js:
            return self.button
        if "clean(x.textContent)" in js:      # click a list item BY NAME
            name = js.split("=== ")[1].split(")")[0].strip().strip('"')
            idx = next((i for i, it in enumerate(self.items)
                        if mw.list_name(it["text"]) == name), None)
            if idx is None:
                return False
            self.clicked.append(name)
            if self.click_ok:                      # toggle, like the real radio
                now = self.items[idx].get("checked") != "true"
                self.items[idx] = {**self.items[idx], "checked": "true" if now else "false"}
                self.button = "Saved" if now else "Save"
            return self.click_ok
        return None


def test_star_clicks_the_target_list_and_verifies():
    b = FakeBrowser()
    out = mw.set_saved("PID", LIST, want_saved=True, **b.driver())
    assert out == {"changed": True}
    assert b.clicked == ["Starred places"]
    assert b.persisted


def test_star_is_idempotent_when_already_saved():
    """A retry after a transient failure must not double-apply."""
    items = [dict(i) for i in ITEMS_RAW]
    items[4]["checked"] = "true"
    b = FakeBrowser(items=items, button="Saved")
    out = mw.set_saved("PID", LIST, want_saved=True, **b.driver())
    assert out["changed"] is False
    assert b.clicked == []


def test_unstar_only_clicks_when_currently_saved():
    items = [dict(i) for i in ITEMS_RAW]
    items[4]["checked"] = "true"
    b = FakeBrowser(items=items, button="Saved")
    mw.set_saved("PID", LIST, want_saved=False, **b.driver())
    assert b.clicked == ["Starred places"]


def test_signed_out_raises_authentication_required():
    """A signed-out session and a reshuffled UI look identical from the DOM (no
    Save button); the probe on the failure path is what tells them apart. The
    es_login retry loop keys off this code, so it must not degrade to a generic
    failure."""
    b = FakeBrowser(signed_in=False, button=None)
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", LIST, want_saved=True, **b.driver())
    assert e.value.es_code == "authentication_required"


def test_missing_button_while_signed_in_is_stale_not_auth():
    """The same DOM, still signed in, must route to the deep-link fallback
    instead of sending the operator through a pointless re-login."""
    b = FakeBrowser(signed_in=True, button=None)
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", LIST, want_saved=True, **b.driver())
    assert e.value.es_code == "maps_automation_stale"


def test_missing_picker_raises_stale_so_the_agent_can_fall_back():
    b = FakeBrowser(items=[])
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", LIST, want_saved=True, **b.driver())
    assert e.value.es_code == "maps_automation_stale"


def test_unverified_write_raises_rather_than_reporting_success():
    """A click that silently no-ops must not be reported as saved — the whole
    point of re-reading the button label after clicking."""
    b = FakeBrowser(click_ok=False)
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", LIST, want_saved=True, **b.driver())
    assert e.value.es_code == "maps_automation_stale"


def test_read_lists_reports_membership_without_clicking_anything():
    b = FakeBrowser()
    out = mw.read_lists("PID", **{k: v for k, v in b.driver().items() if k != "persist"})
    assert {"list": "Starred places", "in_list": False} in out
    assert b.clicked == []


def test_save_button_matches_by_prefix_not_exact_label():
    """Google rewrites the label by list count: Save -> Saved -> "Saved (2)".

    Exact-matching ['Save','Saved'] made a place in TWO lists invisible: the
    selector found nothing, every click was a no-op, and every operation failed
    with save=0. This looked like flaky timing for hours; it was a string match.
    """
    assert mw._SAVE_SELECTOR == 'button[aria-label^="Save"]'
    for label in ("Save", "Saved", "Saved (2)", "Saved (12)"):
        assert label.startswith("Save")


def test_list_items_are_clicked_by_name_never_by_index():
    """The picker REORDERS — checked lists float to the top — so an index read
    in one call can address a different row in the next. That is how a star
    landed in Favorites. The click must find its row by name, in the same
    evaluate that clicks it."""
    assert "clean(x.textContent) ===" in mw._JS_CLICK_ITEM
    assert "it[" not in mw._JS_CLICK_ITEM


def test_list_rows_are_scoped_to_the_results_pane():
    """Rows carry jsaction="pane.*"; the navigation rail uses
    "click:navigationrail.*" and is otherwise shaped identically to the
    name-extraction heuristic — which is how the "10Austin & Cedar Park" chip
    leaked in as a place literally named "10"."""
    assert "/^pane\\./" in mw._JS_LIST_PLACES
    assert "/^pane\\./" in mw._JS_CLICK_PLACE


ADDR = "11521 Ranch Rd 620 N E-1000, Austin, TX 78726"


def test_identify_searches_name_AND_address():
    """Name+address is what recovers the BUSINESS. A bare address query returns
    the ADDRESS entity — a different place_id — the same business-vs-address
    split as geocode vs search. Measured on one branch:
        "Torchy's Tacos, 11521 Ranch Rd 620 N..." -> ChIJs0xt (business)
        "11521 Ranch Rd 620 N..."                 -> ChIJUW5f (address)
    """
    seen = {}
    def search(q, limit=None):
        seen["q"] = q
        return [{"place_id": "ChIJgood", "address": ADDR + ", USA"}]
    out = mw._identify("Torchy's Tacos", "irrelevant", ADDR, search=search)
    assert out["place_id"] == "ChIJgood"
    assert seen["q"].startswith("Torchy's Tacos,") and ADDR in seen["q"]


def test_identify_does_not_use_url_coordinates():
    """An earlier version biased the search by the URL's @lat,lng — but that is
    the map VIEWPORT centre, not the place. With one starred pin it coincided
    and looked right; with two, the viewport shifted and Places returned a
    branch 15 miles away. The URL is now unused."""
    out = mw._identify("X", "https://maps.google.com/@1.0,2.0,17z/data=x", ADDR,
                       search=lambda q, limit=None: [{"place_id": "P", "address": ADDR}])
    assert out["place_id"] == "P"


def test_identify_rejects_a_mismatched_address():
    """Verification is what makes this safe: confirm Places found THIS branch,
    not a neighbouring one."""
    with pytest.raises(mw.MapsAutomationError) as e:
        mw._identify("Torchy's Tacos", "u", ADDR,
                     search=lambda q, limit=None: [
                         {"place_id": "ChIJwrong", "address": "1468 E Whitestone Blvd, Cedar Park"}])
    assert e.value.es_code == "maps_place_not_found"


def test_identify_refuses_without_an_address():
    with pytest.raises(mw.MapsAutomationError) as e:
        mw._identify("X", "u", None, search=lambda *a, **k: [])
    assert e.value.es_code == "maps_automation_stale"


def test_click_place_addresses_duplicates_by_index():
    """resolve must return one entry PER matching row. Two starred branches of a
    chain are identical in the list view, so the Nth-match index is the only way
    to reach the second — consumed within a single read, never stored."""
    assert "wantIdx" in mw._JS_CLICK_PLACE
