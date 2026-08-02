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
