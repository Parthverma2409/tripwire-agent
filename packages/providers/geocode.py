"""Area + PIN code -> coordinates, through OpenStreetMap's Nominatim.

Swiggy Dineout needs a latitude and longitude for every search and booking.
Swiggy's own ``recommend_address`` tool stopped being offered to third-party
clients, so this is the fallback. Only the dining area and PIN from Settings are
sent, once; the caller caches the answer. Nominatim's usage policy asks for an
identifying User-Agent and at most one request a second, which a cached
one-off lookup is far below.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Tripwire/0.1 (local booking assistant; github.com/Parthverma2409/tripwire-agent)"

#: (url, headers) -> (status, body). Injected by tests.
Fetch = Callable[[str, dict], tuple[int, bytes]]


class GeocodeError(RuntimeError):
    """The place could not be found, or OpenStreetMap could not be reached."""


def urllib_fetch(url: str, headers: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""
    except (urllib.error.URLError, OSError) as exc:
        raise GeocodeError("OpenStreetMap is unreachable (%s)" % getattr(exc, "reason", exc)) from None


def nominatim(area: str, postal_code: str, fetch: Fetch = urllib_fetch) -> dict:
    """``{latitude, longitude, address}`` for an Indian area and PIN code.

    The PIN alone is tried first (it is the precise part); the area with the PIN
    is the fallback for PINs OpenStreetMap does not index.
    """
    for params in ({"postalcode": postal_code, "country": "India"}, {"q": "%s %s India" % (area, postal_code)}):
        query = urllib.parse.urlencode({**params, "format": "jsonv2", "limit": 1})
        status, raw = fetch("%s?%s" % (SEARCH_URL, query), {"User-Agent": USER_AGENT, "Accept": "application/json"})
        if status != 200:
            raise GeocodeError("OpenStreetMap answered %s" % status)
        try:
            places = json.loads(raw.decode("utf-8") or "[]")
        except (ValueError, UnicodeDecodeError):
            raise GeocodeError("OpenStreetMap sent an unreadable answer") from None
        if places:
            place = places[0]
            return {"latitude": float(place["lat"]), "longitude": float(place["lon"]),
                    "address": place.get("display_name") or area}
    raise GeocodeError("Couldn't find %s (PIN %s) on the map. Check the dining area and PIN in Settings." % (area, postal_code))
