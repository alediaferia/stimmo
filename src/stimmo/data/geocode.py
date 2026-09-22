"""Address geocoding via OSM Nominatim (free, 1 req/s)."""

from __future__ import annotations

import re
import time

import requests

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "stimmo/0.1 (https://github.com/alediaferia/stimmo; stimmo.it)"

# Milano is full of formerly-private roads whose official name keeps a
# Privata/Privato qualifier after the street type ("Via Privata Martiri
# Triestini").  Nominatim indexes a number of them under the bare street name
# only, so an otherwise valid listing address dead-ends at a 400.  We retry
# once with the qualifier dropped.  Deliberately narrow: anchored at the start
# of the address, applied at most once, and only for the street types that
# actually take the qualifier -- this is a documented special case, not a
# general-purpose address normaliser.
_STREET_TYPES = "via|viale|vicolo|piazza|piazzale|largo|corso|strada"
_PRIVATE_QUALIFIER = re.compile(rf"^({_STREET_TYPES})\s+privat[ao]\s+", re.IGNORECASE)

_last_call = 0.0


def _throttle() -> None:
    global _last_call
    delta = time.time() - _last_call
    if delta < 1.1:
        time.sleep(1.1 - delta)
    _last_call = time.time()


def _variants(address: str) -> list[str]:
    """Address spellings to try, most faithful first."""
    out = [address]
    stripped = _PRIVATE_QUALIFIER.sub(r"\1 ", address, count=1)
    if stripped != address:
        out.append(stripped)
    return out


def _query(address: str, city: str) -> tuple[float, float] | None:
    """One throttled Nominatim lookup.  None when the address is not indexed."""
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
        return None
    return float(items[0]["lat"]), float(items[0]["lon"])


def geocode(address: str, *, city: str = "Milano") -> tuple[float, float]:
    for candidate in _variants(address):
        hit = _query(candidate, city)
        if hit is not None:
            return hit
    raise LookupError(f"Could not geocode: {address!r}")
