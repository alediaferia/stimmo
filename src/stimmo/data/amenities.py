"""OpenStreetMap Overpass — count amenities around the property."""

from __future__ import annotations

import random
import time
from importlib.metadata import version as _pkg_version

import requests

from stimmo.models import AmenityItem, AmenityScore

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = (
    f"stimmo/{_pkg_version('stimmo')} "
    "(https://stimmo.it; https://github.com/alediaferia/stimmo; Overpass amenity counter)"
)
_TIMEOUT = 20  # seconds per call
_MAX_RETRY_SLEEP = 2.0  # hard cap on added wait before giving up


class AmenityFetchError(Exception):
    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


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


def _post_overpass(query: str) -> dict:
    """POST a query to Overpass with one auto-retry on transient failures."""
    last_err = "unknown error"
    for attempt in range(1, 3):
        try:
            r = requests.post(
                OVERPASS, data={"data": query}, headers={"User-Agent": UA}, timeout=_TIMEOUT
            )
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(1.5 + random.uniform(0, 0.5))
            continue

        if r.status_code == 429:
            retry_after = float(r.headers.get("Retry-After", 99))
            if attempt < 2 and retry_after <= _MAX_RETRY_SLEEP:
                last_err = "HTTP 429"
                time.sleep(retry_after)
                continue
            raise AmenityFetchError("Overpass rate-limited (HTTP 429)", attempts=attempt)

        if r.status_code >= 500:
            last_err = f"HTTP {r.status_code}"
            if attempt < 2:
                time.sleep(1.5 + random.uniform(0, 0.5))
            continue

        if not r.ok:
            raise AmenityFetchError(f"HTTP {r.status_code}", attempts=attempt)

        data = r.json()
        remark = data.get("remark", "")
        if ("runtime error" in remark or "timeout" in remark) and not data.get("elements"):
            last_err = f"Overpass soft-failure: {remark}"
            if attempt < 2:
                time.sleep(1.5 + random.uniform(0, 0.5))
            continue

        return data

    raise AmenityFetchError(last_err, attempts=2)


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
    return _post_overpass(q)


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


def _centroid(geometry: list[dict]) -> tuple[float, float]:
    lats = [p["lat"] for p in geometry if "lat" in p]
    lons = [p["lon"] for p in geometry if "lon" in p]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _count_elements(data: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for el in data.get("elements", []):
        kind = _classify(el)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def _extract_items(data: dict) -> list[AmenityItem]:
    items: list[AmenityItem] = []
    for el in data.get("elements", []):
        kind = _classify(el)
        if not kind:
            continue
        name: str | None = el.get("tags", {}).get("name")
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif el.get("geometry"):
            try:
                lat, lon = _centroid(el["geometry"])
            except (ZeroDivisionError, KeyError):
                continue
        else:
            continue
        items.append(AmenityItem(kind=kind, name=name, lat=lat, lon=lon))  # type: ignore[arg-type]
    # Metro/tram first (most identity-bearing), then parks, then rest; cap at 12
    order = ["metro", "tram", "park", "supermarket", "school", "pharmacy"]
    items.sort(key=lambda x: order.index(x.kind) if x.kind in order else 99)
    return items[:12]


def _score_for_counts(kind: str, count: int, weight: float = 1.0) -> float:
    if count <= 0:
        return 0.0
    cap = SCORE_CAPS[kind]
    if kind in ("metro", "tram", "pharmacy", "park"):
        return cap * weight
    else:  # supermarket, school — diminishing returns up to 3
        return cap * weight if count >= 3 else cap * weight * (count / 3)


def fetch_amenities(lat: float, lon: float) -> AmenityScore:
    data_500 = _query(lat, lon, radius=500)
    counts_500 = _count_elements(data_500)
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
        items_within_500m=_extract_items(data_500),
    )
