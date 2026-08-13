import httpx
import pytest
from es.capabilities import maps

def test_render_duration():
    assert maps.render_duration("2700s") == "45 min"
    assert maps.render_duration("5400s") == "1 hr 30 min"
    assert maps.render_duration(None) is None

def test_render_distance_metric():
    assert maps.render_distance(50000) == "50.0 km"
    assert maps.render_distance(None) is None

def test_check_status_ok_and_zero_results_pass():
    maps.check_status({"status": "OK"})
    maps.check_status({"status": "ZERO_RESULTS"})

def test_check_status_maps_errors():
    for status, code in [("OVER_QUERY_LIMIT", "quota_exceeded"),
                         ("REQUEST_DENIED", "maps_unauthorized"),
                         ("INVALID_REQUEST", "maps_error")]:
        with pytest.raises(maps.MapsError) as ei:
            maps.check_status({"status": status})
        assert ei.value.es_code == code

def test_api_key_missing_raises(monkeypatch):
    monkeypatch.setattr(maps.config, "load_config", lambda: {})
    with pytest.raises(maps.MapsError) as ei:
        maps.api_key()
    assert ei.value.es_code == "maps_not_configured"

def test_geocode_view_first_result():
    resp = {"status": "OK", "results": [{
        "formatted_address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043, USA",
        "geometry": {"location": {"lat": 37.4222804, "lng": -122.0843428}},
        "place_id": "ChIJRxcAvRO7j4AR6hm6tys8yA8"}]}
    assert maps.geocode_view(resp) == {
        "address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043, USA",
        "lat": 37.4222804, "lng": -122.0843428, "place_id": "ChIJRxcAvRO7j4AR6hm6tys8yA8"}

def test_geocode_view_zero_results_is_none():
    assert maps.geocode_view({"status": "ZERO_RESULTS", "results": []}) is None

def test_search_view_maps_places():
    resp = {"places": [
        {"id": "p1", "displayName": {"text": "Blue Bottle"}, "formattedAddress": "66 Mint St", "rating": 4.5},
        {"id": "p2", "displayName": {"text": "Sightglass"}, "formattedAddress": "270 7th St"}]}
    out = maps.search_view(resp)
    assert out[0] == {"name": "Blue Bottle", "address": "66 Mint St", "place_id": "p1", "rating": 4.5}
    assert out[1] == {"name": "Sightglass", "address": "270 7th St", "place_id": "p2", "rating": None}

def test_search_body_minimal_and_with_options():
    assert maps.search_body("coffee") == {"textQuery": "coffee"}
    b = maps.search_body("coffee", near_latlng=(37.7, -122.0), open_now=True, limit=5)
    assert b["textQuery"] == "coffee" and b["openNow"] is True and b["pageSize"] == 5
    assert b["locationBias"]["circle"]["center"] == {"latitude": 37.7, "longitude": -122.0}

def test_place_view_details():
    resp = {"displayName": {"text": "Googleplex"}, "formattedAddress": "1600 Amphitheatre Pkwy",
            "nationalPhoneNumber": "(650) 253-0000",
            "regularOpeningHours": {"weekdayDescriptions": ["Monday: 9 AM – 5 PM"]},
            "googleMapsUri": "https://maps.google.com/?cid=1"}
    assert maps.place_view(resp) == {
        "name": "Googleplex", "address": "1600 Amphitheatre Pkwy", "phone": "(650) 253-0000",
        "hours": ["Monday: 9 AM – 5 PM"], "url": "https://maps.google.com/?cid=1"}

def test_directions_view_renders_duration_and_distance():
    resp = {"routes": [{"duration": "2700s", "distanceMeters": 50000, "description": "US-101 N",
                        "legs": [{"duration": "2700s", "distanceMeters": 50000}]}]}
    assert maps.directions_view(resp) == {"duration": "45 min", "distance": "50.0 km", "summary": "US-101 N"}

def test_directions_view_no_routes_is_none():
    assert maps.directions_view({"routes": []}) is None

def test_routes_body_drive_adds_traffic_pref_others_dont():
    assert maps.routes_body("A", "B", "DRIVE")["routingPreference"] == "TRAFFIC_AWARE"
    assert "routingPreference" not in maps.routes_body("A", "B", "WALK")
    assert maps.routes_body("A", "B", "DRIVE")["origin"] == {"address": "A"}

def test_matrix_view_reassembles_out_of_order_and_maps_labels():
    origins = ["Hotel A", "Hotel B"]
    dests = ["Field 1", "Field 2"]
    elements = [  # deliberately out of order; one ROUTE_NOT_FOUND
        {"originIndex": 1, "destinationIndex": 0, "duration": "480s", "distanceMeters": 5000, "condition": "ROUTE_EXISTS", "status": {}},
        {"originIndex": 0, "destinationIndex": 0, "duration": "660s", "distanceMeters": 7000, "condition": "ROUTE_EXISTS", "status": {}},
        {"originIndex": 0, "destinationIndex": 1, "condition": "ROUTE_NOT_FOUND", "status": {}},
    ]
    out = maps.matrix_view(elements, origins, dests)
    assert {"origin": "Hotel B", "destination": "Field 1", "duration": "8 min", "distance": "5.0 km", "ok": True} in out
    nf = [e for e in out if e["origin"] == "Hotel A" and e["destination"] == "Field 1"][0]
    assert nf["ok"] is True
    nf2 = [e for e in out if e["origin"] == "Hotel A" and e["destination"] == "Field 2"][0]
    assert nf2["ok"] is False and nf2["duration"] is None

def test_matrix_body_wraps_waypoints():
    b = maps.matrix_body(["A"], ["B", "C"], "DRIVE")
    assert b["origins"] == [{"waypoint": {"address": "A"}}]
    assert b["destinations"] == [{"waypoint": {"address": "B"}}, {"waypoint": {"address": "C"}}]

def test_matrix_over_address_cap_raises():
    with pytest.raises(maps.MapsError) as ei:
        maps.distance_matrix(["a"] * 30, ["b"] * 30)   # 60 addr > 50
    assert ei.value.es_code == "maps_error"

def test_matrix_view_handles_omitted_zero_indices():
    # proto3 JSON omits default-valued ints: originIndex=0/destinationIndex=0 is dropped
    origins = ["Hotel A"]
    dests = ["Field 1"]
    elements = [{"duration": "300s", "distanceMeters": 3000, "condition": "ROUTE_EXISTS", "status": {}}]
    out = maps.matrix_view(elements, origins, dests)
    assert out == [{"origin": "Hotel A", "destination": "Field 1", "duration": "5 min",
                    "distance": "3.0 km", "ok": True}]

def test_geocode_http_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(maps, "api_key", lambda: "AIzaSuperSecretKey")

    def fake_get(url, params=None, timeout=None):
        request = httpx.Request("GET", url, params=params)
        response = httpx.Response(403, request=request, text="permission denied")
        raise httpx.HTTPStatusError("bad status", request=request, response=response)

    monkeypatch.setattr(maps.httpx, "get", fake_get)
    with pytest.raises(maps.MapsError) as ei:
        maps.geocode("1600 Amphitheatre Pkwy")
    assert ei.value.es_code == "maps_error"
    msg = str(ei.value)
    assert "AIzaSuperSecretKey" not in msg
    assert "key=" not in msg


def test_geocode_omits_timezone_by_default(monkeypatch):
    """Opt-in: the common geocode (including es_weather's) keeps a stable shape
    and never loads the timezone polygon data."""
    monkeypatch.setattr(maps, "geocode_view", lambda r: {"address": "A", "lat": 37.77, "lng": -122.41})
    monkeypatch.setattr(maps, "api_key", lambda: "k")
    monkeypatch.setattr(maps.httpx, "get", lambda *a, **k: _Resp({}))
    assert "timezone" not in maps.geocode("SF")


def test_geocode_include_timezone_resolves_the_locations_zone(monkeypatch):
    monkeypatch.setattr(maps, "geocode_view", lambda r: {"address": "A", "lat": 37.7749, "lng": -122.4194})
    monkeypatch.setattr(maps, "api_key", lambda: "k")
    monkeypatch.setattr(maps.httpx, "get", lambda *a, **k: _Resp({}))
    assert maps.geocode("SF", include_timezone=True)["timezone"] == "America/Los_Angeles"


def test_timezone_at_handles_a_no_dst_zone():
    # Phoenix is the classic trap: America/Phoenix, not America/Denver.
    assert maps.timezone_at(33.4484, -112.0740) == "America/Phoenix"


class _Resp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p
