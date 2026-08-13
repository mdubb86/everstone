import httpx
from es import config

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

_STATUS_ERRORS = {"OVER_QUERY_LIMIT": "quota_exceeded", "REQUEST_DENIED": "maps_unauthorized"}
_SEARCH_MASK = "places.id,places.displayName,places.formattedAddress"
_DETAILS_MASK = "displayName,formattedAddress,nationalPhoneNumber,regularOpeningHours,googleMapsUri"
_ROUTES_MASK = "routes.duration,routes.distanceMeters,routes.description"
_MATRIX_MASK = "originIndex,destinationIndex,duration,distanceMeters,condition,status"


class MapsError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.es_code = code


def api_key():
    key = (config.maps_config() or {}).get("api_key")
    if not key:
        raise MapsError("maps_not_configured", "maps.api_key is not set in config.yaml")
    return key


def check_status(resp):  # legacy Geocoding "status" field
    s = (resp or {}).get("status", "")
    if s in ("OK", "ZERO_RESULTS"):
        return
    raise MapsError(_STATUS_ERRORS.get(s, "maps_error"), (resp or {}).get("error_message") or s)


def render_duration(dur):
    if not (isinstance(dur, str) and dur.endswith("s")):
        return None
    mins = round(float(dur[:-1]) / 60)
    h, m = divmod(mins, 60)
    return f"{h} hr {m} min" if h else f"{m} min"


def render_distance(meters):
    if meters is None:
        return None
    return f"{meters / 1000:.1f} km"


_TF = None


def timezone_at(lat, lng):
    """lat/lng -> IANA zone, offline via timezonefinder.

    Chosen over Google's Time Zone API: that would be a second Maps SKU needing
    its own console enablement AND its own iron-proxy secret binding — the exact
    two-step that blocked this work. lat/lng -> zone is a stable lookup where
    Google's authority buys little. Lazy-loaded: the polygon data is tens of MB
    and most geocodes never ask for a zone.
    """
    global _TF
    if _TF is None:
        from timezonefinder import TimezoneFinder
        _TF = TimezoneFinder()
    return _TF.timezone_at(lat=lat, lng=lng)


def geocode_view(resp):
    check_status(resp)
    results = (resp or {}).get("results") or []
    if not results:
        return None
    r = results[0]
    loc = (r.get("geometry") or {}).get("location") or {}
    return {"address": r.get("formatted_address"), "lat": loc.get("lat"),
            "lng": loc.get("lng"), "place_id": r.get("place_id")}


def geocode(query, include_timezone=False):
    try:
        r = httpx.get(_GEOCODE_URL, params={"address": query, "key": api_key()}, timeout=20)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise MapsError("maps_error", f"Geocoding request failed: HTTP {e.response.status_code}")
    view = geocode_view(r.json())
    # Opt-in so the common geocode (including the one es_weather makes) keeps a
    # stable return shape and skips loading the timezone polygon data.
    if view and include_timezone and view.get("lat") is not None:
        view["timezone"] = timezone_at(view["lat"], view["lng"])
    return view


def search_view(resp):
    return [{"name": (p.get("displayName") or {}).get("text"),
             "address": p.get("formattedAddress"), "place_id": p.get("id"),
             "rating": p.get("rating")} for p in (resp or {}).get("places") or []]


def search_body(query, near_latlng=None, open_now=False, limit=None):
    body = {"textQuery": query}
    if open_now:
        body["openNow"] = True
    if limit:
        body["pageSize"] = limit
    if near_latlng:
        body["locationBias"] = {"circle": {"center": {"latitude": near_latlng[0],
                                                       "longitude": near_latlng[1]}, "radius": 5000.0}}
    return body


def _new_post(url, body, field_mask):
    r = httpx.post(url, json=body, timeout=20, headers={
        "X-Goog-Api-Key": api_key(), "X-Goog-FieldMask": field_mask,
        "Content-Type": "application/json"})
    if r.status_code == 429:
        raise MapsError("quota_exceeded", "Maps API daily quota reached")
    if r.status_code in (401, 403):
        raise MapsError("maps_unauthorized", r.text[:200])
    r.raise_for_status()
    return r.json()


def search(query, near=None, open_now=False, limit=None, include_rating=False):
    near_latlng = None
    if near:
        g = geocode(near)
        if g:
            near_latlng = (g["lat"], g["lng"])
    mask = _SEARCH_MASK + (",places.rating" if include_rating else "")
    return search_view(_new_post(_PLACES_SEARCH_URL, search_body(query, near_latlng, open_now, limit), mask))


def place_view(resp):
    r = resp or {}
    return {"name": (r.get("displayName") or {}).get("text"), "address": r.get("formattedAddress"),
            "phone": r.get("nationalPhoneNumber"),
            "hours": (r.get("regularOpeningHours") or {}).get("weekdayDescriptions"),
            "url": r.get("googleMapsUri")}


def place(place_id):
    r = httpx.get(_PLACE_DETAILS_URL.format(place_id=place_id), timeout=20,
                  headers={"X-Goog-Api-Key": api_key(), "X-Goog-FieldMask": _DETAILS_MASK})
    if r.status_code == 429:
        raise MapsError("quota_exceeded", "Maps API daily quota reached")
    if r.status_code in (401, 403):
        raise MapsError("maps_unauthorized", r.text[:200])
    r.raise_for_status()
    return place_view(r.json())


def directions_view(resp):
    routes = (resp or {}).get("routes") or []
    if not routes:
        return None
    r = routes[0]
    summary = r.get("description") or None
    return {"duration": render_duration(r.get("duration")),
            "distance": render_distance(r.get("distanceMeters")), "summary": summary}


def routes_body(origin, destination, mode):
    body = {"origin": {"address": origin}, "destination": {"address": destination}, "travelMode": mode}
    if mode in ("DRIVE", "TWO_WHEELER"):
        body["routingPreference"] = "TRAFFIC_AWARE"
    return body


def directions(origin, destination, mode="DRIVE"):
    return directions_view(_new_post(_ROUTES_URL, routes_body(origin, destination, mode), _ROUTES_MASK))


def matrix_view(elements, origins, destinations):
    out = []
    for e in elements or []:
        ok = e.get("condition") == "ROUTE_EXISTS"
        out.append({"origin": origins[e.get("originIndex", 0)], "destination": destinations[e.get("destinationIndex", 0)],
                    "duration": render_duration(e.get("duration")) if ok else None,
                    "distance": render_distance(e.get("distanceMeters")) if ok else None, "ok": ok})
    return out


def matrix_body(origins, destinations, mode):
    body = {"origins": [{"waypoint": {"address": o}} for o in origins],
            "destinations": [{"waypoint": {"address": d}} for d in destinations], "travelMode": mode}
    if mode in ("DRIVE", "TWO_WHEELER"):
        body["routingPreference"] = "TRAFFIC_AWARE"
    return body


def distance_matrix(origins, destinations, mode="DRIVE"):
    if len(origins) + len(destinations) > 50:
        raise MapsError("maps_error", "too many places: address/place-id origins+destinations must be <= 50")
    if len(origins) * len(destinations) > 625:
        raise MapsError("maps_error", "matrix too large: origins x destinations must be <= 625")
    elements = _new_post(_MATRIX_URL, matrix_body(origins, destinations, mode), _MATRIX_MASK)
    return matrix_view(elements, origins, destinations)
