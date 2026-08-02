from urllib.parse import quote, urlencode


def build_deeplink(vault_name: str, note_path: str) -> str:
    note_path = note_path.lstrip("/")
    return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(note_path, safe='')}"


# Universal Google Maps URLs (api=1) — cross-platform: open the Maps app on phone/car (or web),
# route on-device for free with full Google data. No API key. The agent hands these to Telegram;
# they're also the fallback whenever a Maps-Platform read cap is hit or a browser save fails.

def maps_search_link(query: str, place_id: str = None) -> str:
    """Open a place/search in Maps. Pass place_id to pin the exact place (query is the label)."""
    params = {"api": "1", "query": query}
    if place_id:
        params["query_place_id"] = place_id
    return "https://www.google.com/maps/search/?" + urlencode(params)


def maps_directions_link(destination: str, origin: str = None, mode: str = "driving") -> str:
    """Open navigation to destination (from origin if given). mode: driving|walking|bicycling|transit."""
    params = {"api": "1", "destination": destination}
    if origin:
        params["origin"] = origin
    params["travelmode"] = mode
    return "https://www.google.com/maps/dir/?" + urlencode(params)
