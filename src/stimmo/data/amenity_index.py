"""Bundled OpenStreetMap amenity index — the offline stand-in for the Overpass query.

``assets/amenities.csv.gz`` (built by ``scripts/refresh_amenities.py``, ODbL, see
``assets/amenities.LICENSE``) holds the same node/way objects ``amenities._query`` asks
Overpass for.  This module loads it into per-kind grid buckets (stdlib only) and answers
``elements_within(lat, lon, radius)`` with an Overpass-shaped ``{"elements": [...]}`` dict,
so ``amenities._score_from_data`` derives the 500 m counts, the ring, the score and the map
items from it exactly as it does from a live response.  No scoring logic lives here.

``around:R`` semantics, as Overpass applies them: a node matches within R of itself; a way
matches when any of its segments is within R.  Ways also match when the point lies inside the
polygon or its vertex-mean centroid is within R (the rules ``amenities._distance_m`` uses for
the 500 m subset), so the 1000 m set always contains the 500 m one.

Gated by ``STIMMO_AMENITIES_BUNDLED`` (unset/``0``/``false`` = off); see ``bundled_enabled``.
"""

from __future__ import annotations

import csv
import gzip
import itertools
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

_ASSET = files("stimmo.data.assets") / "amenities.csv.gz"
_MANIFEST = files("stimmo.data.assets") / "manifest.json"

_R = 6371008.8  # same sphere as amenities._haversine_m
_M_PER_DEG = _R * math.pi / 180
_CELL_DEG = 0.01  # ~1.1 km of latitude; a 1 km query touches at most 3x3 cells

# Minimal tags that make ``amenities._classify`` return the stored kind again.
_TAGS = {
    "metro": {"station": "subway"},
    "tram": {"railway": "tram_stop"},
    "park": {"leisure": "park"},
    "supermarket": {"shop": "supermarket"},
    "school": {"amenity": "school"},
    "pharmacy": {"amenity": "pharmacy"},
}


def bundled_enabled() -> bool:
    """True when ``STIMMO_AMENITIES_BUNDLED`` is set to something other than 0/false/no/off."""
    return os.environ.get("STIMMO_AMENITIES_BUNDLED", "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


@dataclass(frozen=True, slots=True)
class _Feature:
    kind: str
    osm_type: str
    osm_id: int
    name: str
    lat: float  # node position; for a way, the vertex-mean centroid
    lon: float
    verts: tuple[tuple[float, float], ...]  # (lat, lon) per vertex; empty for a node
    bbox: tuple[float, float, float, float]  # south, west, north, east


@dataclass(frozen=True, slots=True)
class AmenityIndex:
    features: tuple[_Feature, ...]
    grid: dict[tuple[int, int], tuple[int, ...]]

    def __len__(self) -> int:
        return len(self.features)


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / _CELL_DEG), math.floor(lon / _CELL_DEG)


def _parse_geom(geom: str) -> tuple[tuple[float, float], ...]:
    out = []
    for pair in geom.split(";"):
        a, b = pair.split(" ")
        out.append((float(a), float(b)))
    return tuple(out)


def build_index(rows) -> AmenityIndex:
    """Build the index from an iterable of CSV dict rows (columns as in the asset)."""
    feats: list[_Feature] = []
    grid: dict[tuple[int, int], list[int]] = {}
    for row in rows:
        kind = row["kind"]
        if kind not in _TAGS:
            continue
        if row["geom"]:
            verts = _parse_geom(row["geom"])
            lat = sum(v[0] for v in verts) / len(verts)
            lon = sum(v[1] for v in verts) / len(verts)
            bbox = (
                min(v[0] for v in verts),
                min(v[1] for v in verts),
                max(v[0] for v in verts),
                max(v[1] for v in verts),
            )
        else:
            verts = ()
            lat, lon = float(row["lat"]), float(row["lon"])
            bbox = (lat, lon, lat, lon)
        idx = len(feats)
        feats.append(
            _Feature(kind, row["osm_type"], int(row["osm_id"]), row["name"], lat, lon, verts, bbox)
        )
        (c0, c1), (c2, c3) = _cell(bbox[0], bbox[1]), _cell(bbox[2], bbox[3])
        for ci in range(c0, c2 + 1):
            for cj in range(c1, c3 + 1):
                grid.setdefault((ci, cj), []).append(idx)
    return AmenityIndex(tuple(feats), {k: tuple(v) for k, v in grid.items()})


@cache
def load() -> AmenityIndex:
    """Load (once per process) the bundled asset.  Raises OSError when it is missing."""
    with _ASSET.open("rb") as raw, gzip.open(raw, "rt", encoding="utf-8", newline="") as fh:
        return build_index(csv.DictReader(fh))


def snapshot() -> str | None:
    """The OSM snapshot date (``osm_snapshot`` in the manifest), if recorded."""
    try:
        with _MANIFEST.open("rb") as fh:
            value = json.load(fh).get("osm_snapshot")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _R * math.asin(math.sqrt(a))


def _inside(lat: float, lon: float, verts) -> bool:
    inside = False
    n = len(verts)
    for i in range(n):
        (alat, alon), (blat, blon) = verts[i], verts[(i + 1) % n]
        if (alon > lon) != (blon > lon):
            t = (lon - alon) / (blon - alon)
            if lat < alat + t * (blat - alat):
                inside = not inside
    return inside


def _min_segment_m(lat: float, lon: float, verts) -> float:
    """Distance in metres from the point to the polyline (local equirectangular plane)."""
    kx = _M_PER_DEG * math.cos(math.radians(lat))
    ky = _M_PER_DEG
    pts = [((v[1] - lon) * kx, (v[0] - lat) * ky) for v in verts]
    if len(pts) == 1:
        return math.hypot(*pts[0])
    best = math.inf
    for (ax, ay), (bx, by) in itertools.pairwise(pts):
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def _element(f: _Feature) -> dict:
    tags = dict(_TAGS[f.kind])
    if f.name:
        tags["name"] = f.name
    if f.osm_type == "node":
        return {"type": "node", "id": f.osm_id, "lat": f.lat, "lon": f.lon, "tags": tags}
    return {
        "type": "way",
        "id": f.osm_id,
        "tags": tags,
        "geometry": [{"lat": a, "lon": b} for a, b in f.verts],
    }


def elements_within(lat: float, lon: float, radius_m: float = 1000, *, index=None) -> dict:
    """Overpass-shaped ``{"elements": [...]}`` of the bundled features around the point."""
    idx = index if index is not None else load()
    dlat = radius_m / _M_PER_DEG * 1.02
    dlon = dlat / max(0.01, math.cos(math.radians(lat)))
    (c0, c1), (c2, c3) = _cell(lat - dlat, lon - dlon), _cell(lat + dlat, lon + dlon)
    seen: set[int] = set()
    kept: list[_Feature] = []
    for ci in range(c0, c2 + 1):
        for cj in range(c1, c3 + 1):
            for i in idx.grid.get((ci, cj), ()):
                if i in seen:
                    continue
                seen.add(i)
                f = idx.features[i]
                if not f.verts:
                    if _haversine_m(lat, lon, f.lat, f.lon) <= radius_m:
                        kept.append(f)
                    continue
                s, w, n, e = f.bbox
                if n < lat - dlat or s > lat + dlat or e < lon - dlon or w > lon + dlon:
                    continue
                if (
                    (len(f.verts) >= 3 and _inside(lat, lon, f.verts))
                    or _min_segment_m(lat, lon, f.verts) <= radius_m
                    or _haversine_m(lat, lon, f.lat, f.lon) <= radius_m
                ):
                    kept.append(f)
    kept.sort(key=lambda f: (f.osm_type, f.osm_id))
    return {"elements": [_element(f) for f in kept]}
