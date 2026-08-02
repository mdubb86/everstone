from es.deeplink import maps_search_link, maps_directions_link

# Universal cross-platform Google Maps URLs (open the Maps app on phone/car, or web). No API key.


def test_search_link_basic():
    assert maps_search_link("Blue Bottle Coffee") == \
        "https://www.google.com/maps/search/?api=1&query=Blue+Bottle+Coffee"


def test_search_link_with_place_id_is_exact():
    url = maps_search_link("Blue Bottle", place_id="ChIJabc123")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=Blue+Bottle")
    assert "query_place_id=ChIJabc123" in url


def test_directions_link_destination_only_defaults_driving():
    assert maps_directions_link("Golden Gate Bridge") == \
        "https://www.google.com/maps/dir/?api=1&destination=Golden+Gate+Bridge&travelmode=driving"


def test_directions_link_with_origin_and_mode():
    url = maps_directions_link("B Street", origin="A Street", mode="walking")
    assert "origin=A+Street" in url and "destination=B+Street" in url and "travelmode=walking" in url
