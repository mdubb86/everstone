import httpx
from es import config

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

_STATUS_ERRORS = {"OVER_QUERY_LIMIT": "quota_exceeded", "REQUEST_DENIED": "maps_unauthorized"}


class MapsError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.es_code = code


def api_key():
    key = (config.load_config().get("maps") or {}).get("api_key")
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


def geocode_view(resp):
    check_status(resp)
    results = (resp or {}).get("results") or []
    if not results:
        return None
    r = results[0]
    loc = (r.get("geometry") or {}).get("location") or {}
    return {"address": r.get("formatted_address"), "lat": loc.get("lat"),
            "lng": loc.get("lng"), "place_id": r.get("place_id")}


def geocode(query):
    r = httpx.get(_GEOCODE_URL, params={"address": query, "key": api_key()}, timeout=20)
    r.raise_for_status()
    return geocode_view(r.json())
