from __future__ import annotations

import pytest

from stimmo.data import amenities, amenity_index
from stimmo.models import AmenityScore

HEADER = ["kind", "osm_type", "osm_id", "name", "lat", "lon", "geom"]


def _row(kind, osm_type, osm_id, name="", lat="", lon="", geom=""):
    return dict(zip(HEADER, [kind, osm_type, osm_id, name, lat, lon, geom], strict=True))


# A ~100 m square "park" with > 20 vertices around (45.4500, 9.1900); the closing vertex repeats.
def _park_geom():
    s, w, n, e = 45.4495, 9.1894, 45.4505, 9.1906
    pts = []
    for i in range(6):  # 6 per side -> 24 vertices
        f = i / 6
        pts += [(s, w + (e - w) * f)]
    for i in range(6):
        pts += [(s + (n - s) * i / 6, e)]
    for i in range(6):
        pts += [(n, e - (e - w) * i / 6)]
    for i in range(6):
        pts += [(n - (n - s) * i / 6, w)]
    pts.append(pts[0])
    return ";".join(f"{a:.6f} {b:.6f}" for a, b in pts)


ROWS = [
    _row("metro", "node", 1, "Duomo", "45.4642", "9.1900"),  # ~1.5 km away -> excluded
    _row("metro", "node", 2, "Near", "45.4520", "9.1900"),  # ~220 m
    _row("pharmacy", "node", 3, "Ring pharmacy", "45.4570", "9.1900"),  # ~780 m
    _row("school", "way", 4, "Big school", geom="45.4540 9.1990;45.4540 9.2000;45.4550 9.2000"),
    _row("park", "way", 5, "Parco", geom=_park_geom()),  # point inside
]
LAT, LON = 45.4500, 9.1900


@pytest.fixture
def index():
    return amenity_index.build_index(ROWS)


def test_elements_are_overpass_shaped_and_classifiable(index):
    els = amenity_index.elements_within(LAT, LON, 1000, index=index)["elements"]
    by_id = {(e["type"], e["id"]): e for e in els}
    assert ("node", 1) not in by_id
    assert {k for k in by_id} == {("node", 2), ("node", 3), ("way", 4), ("way", 5)}
    assert amenities._classify(by_id[("node", 2)]) == "metro"
    assert amenities._classify(by_id[("way", 5)]) == "park"  # > 20 vertices survives
    assert by_id[("node", 2)]["tags"]["name"] == "Near"


def test_scoring_reuses_amenities_helpers(index):
    data = amenity_index.elements_within(LAT, LON, 1000, index=index)
    s = amenities._score_from_data(data, LAT, LON)
    assert isinstance(s, AmenityScore)
    assert s.metro_within_500m == 1
    assert s.parks_large_within_500m == 1  # point inside the polygon
    assert s.pharmacies_within_500m == 0 and s.pharmacies_500_1000m == 1
    assert s.schools_within_500m == 0 and s.schools_500_1000m == 1  # ~830 m


def test_way_matches_on_segment_not_only_vertices():
    # Long segment passing ~50 m from the point, vertices > 1 km away on both sides.
    idx = amenity_index.build_index(
        [_row("school", "way", 9, "Long", geom="45.4505 9.1700;45.4505 9.2100")]
    )
    els = amenity_index.elements_within(LAT, LON, 1000, index=idx)["elements"]
    assert [e["id"] for e in els] == [9]
    assert amenity_index.elements_within(LAT + 0.02, LON, 1000, index=idx)["elements"] == []


def test_flag_parsing(monkeypatch):
    for v, expected in [("", False), ("0", False), ("false", False), ("1", True), ("true", True)]:
        monkeypatch.setenv("STIMMO_AMENITIES_BUNDLED", v)
        assert amenity_index.bundled_enabled() is expected
    monkeypatch.delenv("STIMMO_AMENITIES_BUNDLED")
    assert amenity_index.bundled_enabled() is False


def test_real_asset_loads_and_has_every_kind():
    idx = amenity_index.load()
    assert {f.kind for f in idx.features} == set(amenities.SCORE_CAPS)
    assert amenity_index.snapshot()


def test_flag_switches_the_source(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Overpass must not be called")

    monkeypatch.setattr(amenities, "_query", boom)
    monkeypatch.setenv("STIMMO_AMENITIES_BUNDLED", "1")
    s = amenities._fetch_amenities_uncached(45.4642, 9.1900)  # Duomo
    assert s.metro_within_500m >= 1

    # Off: the Overpass path is used.
    monkeypatch.delenv("STIMMO_AMENITIES_BUNDLED")
    monkeypatch.setattr(amenities, "_query", lambda *a, **k: {"elements": []})
    assert amenities._fetch_amenities_uncached(45.4642, 9.1900).metro_within_500m == 0


def test_flag_covers_resolve_and_missing_asset_is_a_fetch_error(monkeypatch):
    monkeypatch.setenv("STIMMO_AMENITIES_BUNDLED", "1")
    monkeypatch.setattr(
        amenities, "_query", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network"))
    )
    status, score, err = amenities.resolve(45.4342, 9.2234, 5.0)
    assert (status, err) == ("ok", None) and score is not None

    def missing(*a, **k):
        raise FileNotFoundError("amenities.csv.gz")

    monkeypatch.setattr(amenity_index, "elements_within", missing)
    with pytest.raises(amenities.AmenityFetchError):
        amenities._fetch_amenities_uncached(45.4342, 9.2234)
