"""Maps writes (M2) — flow logic, no browser.

The DOM contract these encode was read off the live Maps UI on 2026-08-14:
a button with aria-label "Save"/"Saved", and one [role=menuitemradio] per list
whose textContent is "<name>Private · N places".
"""
import pytest

from es.capabilities import maps_write as mw

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
        "Starred")["name"] == "Starred places"


def test_parse_items_indexes_and_flags_checked():
    items = mw.parse_items(ITEMS_RAW[:2] + [{"text": "XPrivate", "checked": "true"}])
    assert [i["name"] for i in items] == ["Travel plans", "Nail", "X"]
    assert [i["index"] for i in items] == [0, 1, 2]
    assert items[2]["checked"] is True


def test_shipped_default_prefix_matches_googles_wording():
    """config ships `Starred`; Google's list is "Starred places". The default
    must select it without hardcoding Google's exact wording."""
    assert mw.choose_list(mw.parse_items(ITEMS_RAW), "Starred")["name"] == "Starred places"


def test_exact_match_wins_over_prefix():
    items = mw.parse_items([{"text": "SavedPrivate", "checked": "false"},
                            {"text": "Saved placesPrivate", "checked": "false"}])
    assert mw.choose_list(items, "Saved")["name"] == "Saved"


def test_ambiguous_prefix_errors_rather_than_guessing():
    items = mw.parse_items([{"text": "Want to goPrivate", "checked": "false"},
                            {"text": "Want to seePrivate", "checked": "false"}])
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.choose_list(items, "Want to")
    assert e.value.es_code == "maps_list_ambiguous"


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
            if "menuitemradio" in js:
                return len(self.items)
            return 0 if self.button is None else 1
        if self.button is None and "['Save','Saved'].includes" in js:
            return False if "b.click()" in js else None
        if "aria-checked" in js and "menuitemradio" in js and "click" not in js:
            return self.items
        if "KeyboardEvent" in js:
            return True
        if "['Save','Saved'].includes" in js and "b.click()" in js:
            return True
        if "['Save','Saved'].includes" in js:
            return self.button
        if "it[" in js:                       # click a list item
            idx = int(js.split("it[")[1].split("]")[0])
            self.clicked.append(idx)
            if self.click_ok:                      # toggle, like the real radio
                now = self.items[idx].get("checked") != "true"
                self.items[idx] = {**self.items[idx], "checked": "true" if now else "false"}
                self.button = "Saved" if now else "Save"
            return self.click_ok
        return None


def test_star_clicks_the_target_list_and_verifies():
    b = FakeBrowser()
    out = mw.set_saved("PID", "Starred", want_saved=True, **b.driver())
    assert out == {"place_id": "PID", "list": "Starred places", "saved": True, "changed": True}
    assert b.clicked == [4]
    assert b.persisted


def test_star_is_idempotent_when_already_saved():
    """A retry after a transient failure must not double-apply."""
    items = [dict(i) for i in ITEMS_RAW]
    items[4]["checked"] = "true"
    b = FakeBrowser(items=items, button="Saved")
    out = mw.set_saved("PID", "Starred", want_saved=True, **b.driver())
    assert out["changed"] is False
    assert b.clicked == []


def test_unstar_only_clicks_when_currently_saved():
    items = [dict(i) for i in ITEMS_RAW]
    items[4]["checked"] = "true"
    b = FakeBrowser(items=items, button="Saved")
    mw.set_saved("PID", "Starred", want_saved=False, **b.driver())
    assert b.clicked == [4]


def test_signed_out_raises_authentication_required():
    """A signed-out session and a reshuffled UI look identical from the DOM (no
    Save button); the probe on the failure path is what tells them apart. The
    es_login retry loop keys off this code, so it must not degrade to a generic
    failure."""
    b = FakeBrowser(signed_in=False, button=None)
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", "Starred", want_saved=True, **b.driver())
    assert e.value.es_code == "authentication_required"


def test_missing_button_while_signed_in_is_stale_not_auth():
    """The same DOM, still signed in, must route to the deep-link fallback
    instead of sending the operator through a pointless re-login."""
    b = FakeBrowser(signed_in=True, button=None)
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", "Starred", want_saved=True, **b.driver())
    assert e.value.es_code == "maps_automation_stale"


def test_missing_picker_raises_stale_so_the_agent_can_fall_back():
    b = FakeBrowser(items=[])
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", "Starred", want_saved=True, **b.driver())
    assert e.value.es_code == "maps_automation_stale"


def test_unverified_write_raises_rather_than_reporting_success():
    """A click that silently no-ops must not be reported as saved — the whole
    point of re-reading the button label after clicking."""
    b = FakeBrowser(click_ok=False)
    with pytest.raises(mw.MapsAutomationError) as e:
        mw.set_saved("PID", "Starred", want_saved=True, **b.driver())
    assert e.value.es_code == "maps_automation_stale"


def test_read_lists_reports_membership_without_clicking_anything():
    b = FakeBrowser()
    out = mw.read_lists("PID", **{k: v for k, v in b.driver().items() if k != "persist"})
    assert {"name": "Starred places", "saved": False} in out
    assert b.clicked == []
