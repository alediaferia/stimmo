"""Address geocoding via OSM Nominatim (free, 1 req/s)."""

from __future__ import annotations

import time

import requests

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "stimmo/0.1 (https://github.com/local; pos-research)"

_last_call = 0.0


def _throttle() -> None:
    global _last_call
    delta = time.time() - _last_call
    if delta < 1.1:
        time.sleep(1.1 - delta)
    _last_call = time.time()


def geocode(address: str, *, city: str = "Milano") -> tuple[float, float]:
    _throttle()
    q = f"{address}, {city}, Italy"
    # Bias + restrict to the Milano comune bbox so suburbs with the same street
    # name aren't picked.
    milano_viewbox = "9.04,45.54,9.28,45.38"  # left,top,right,bottom
    r = requests.get(
        NOMINATIM,
        params={
            "q": q,
            "format": "json",
            "limit": 1,
            "countrycodes": "it",
            "viewbox": milano_viewbox,
            "bounded": 1,
        },
        headers={"User-Agent": UA},
        timeout=15,
    )
    r.raise_for_status()
    items = r.json()
    if not items:
        raise LookupError(f"Could not geocode: {address!r}")
    return float(items[0]["lat"]), float(items[0]["lon"])
