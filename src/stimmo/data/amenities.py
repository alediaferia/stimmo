"""OpenStreetMap Overpass — count amenities around the property."""

from __future__ import annotations

import requests

from stimmo.models import AmenityScore

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "stimmo/0.1 (Overpass amenity counter)"

# Caps to keep the +% adjustment bounded.
SCORE_CAPS = {
    "metro": 2.0,
    "tram": 1.0,
    "park": 1.0,
    "supermarket": 1.0,
    "school": 0.5,
    "pharmacy": 0.5,
}
# OMI zones already embed transit/amenity proximity; halved to avoid double-counting.
TOTAL_CAP_PCT = 2.5


def _query(lat: float, lon: float, radius: int = 500) -> dict:
    around = f"around:{radius},{lat},{lon}"
    q = f"""
[out:json][timeout:25];
(
  node["railway"="station"]["station"="subway"]({around});
  node["station"="subway"]({around});
  node["railway"="tram_stop"]({around});
  way["leisure"="park"]({around});
  node["shop"="supermarket"]({around});
  way["shop"="supermarket"]({around});
  node["amenity"="school"]({around});
  way["amenity"="school"]({around});
  node["amenity"="pharmacy"]({around});
);
out tags geom;
"""
    r = requests.post(OVERPASS, data={"data": q}, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.json()


def _classify(el: dict) -> str | None:
    t = el.get("tags", {})
    if t.get("station") == "subway" or (
        t.get("railway") == "station" and t.get("station") == "subway"
    ):
        return "metro"
    if t.get("railway") == "tram_stop":
        return "tram"
    if t.get("leisure") == "park":
        # Only count "large" parks; geometry-based area is overkill — use a node count proxy.
        coords = el.get("geometry") or []
        if len(coords) > 20:
            return "park"
        return None
    if t.get("shop") == "supermarket":
        return "supermarket"
    if t.get("amenity") == "school":
        return "school"
    if t.get("amenity") == "pharmacy":
        return "pharmacy"
    return None


def _count_elements(data: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for el in data.get("elements", []):
        kind = _classify(el)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def _score_for_counts(kind: str, count: int, weight: float = 1.0) -> float:
    if count <= 0:
        return 0.0
    cap = SCORE_CAPS[kind]
    if kind in ("metro", "tram", "pharmacy", "park"):
        return cap * weight
    else:  # supermarket, school — diminishing returns up to 3
        return cap * weight if count >= 3 else cap * weight * (count / 3)


def fetch_amenities(lat: float, lon: float) -> AmenityScore:
    counts_500 = _count_elements(_query(lat, lon, radius=500))
    counts_1000 = _count_elements(_query(lat, lon, radius=1000))
    # Ring = amenities exclusive to the 500–1000 m band
    counts_ring = {k: max(0, counts_1000.get(k, 0) - counts_500.get(k, 0)) for k in SCORE_CAPS}

    raw_score = 0.0
    for kind in SCORE_CAPS:
        raw_score += _score_for_counts(kind, counts_500.get(kind, 0), weight=1.0)
        raw_score += _score_for_counts(kind, counts_ring.get(kind, 0), weight=0.5)

    score = min(raw_score, TOTAL_CAP_PCT)
    return AmenityScore(
        metro_within_500m=counts_500.get("metro", 0),
        tram_within_500m=counts_500.get("tram", 0),
        parks_large_within_500m=counts_500.get("park", 0),
        supermarkets_within_500m=counts_500.get("supermarket", 0),
        schools_within_500m=counts_500.get("school", 0),
        pharmacies_within_500m=counts_500.get("pharmacy", 0),
        metro_500_1000m=counts_ring.get("metro", 0),
        tram_500_1000m=counts_ring.get("tram", 0),
        parks_large_500_1000m=counts_ring.get("park", 0),
        supermarkets_500_1000m=counts_ring.get("supermarket", 0),
        schools_500_1000m=counts_ring.get("school", 0),
        pharmacies_500_1000m=counts_ring.get("pharmacy", 0),
        score_pct=round(score, 2),
    )
