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
