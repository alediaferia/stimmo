"""Parity of the bundled amenity index against recorded live Overpass responses.

``tests/data/amenity_overpass_fixtures.json`` holds the raw response of the Overpass code path
(``amenities._query``) at nine Milan points, recorded once on 2026-11-09.  The bundled asset
comes from an OSM snapshot of 2026-11-08, so a handful of one-off OSM edits can differ; the
tolerance below absorbs that (per kind abs(diff) <= max(1, 15 %), score within 0.3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stimmo.data import amenities, amenity_index
from stimmo.models import AmenityScore

FIXTURES = {
    k: v
    for k, v in json.loads(
        (Path(__file__).parent / "data" / "amenity_overpass_fixtures.json").read_text()
    ).items()
    if not k.startswith("_")
}
COUNT_FIELDS = [f for f, info in AmenityScore.model_fields.items() if info.annotation is int]


def _both(name: str) -> tuple[AmenityScore, AmenityScore]:
    p = FIXTURES[name]
    expected = amenities._score_from_data(p["overpass"], p["lat"], p["lon"])
    got = amenities._score_from_data(
        amenity_index.elements_within(p["lat"], p["lon"], amenities.FAR_M), p["lat"], p["lon"]
    )
    return expected, got


def test_fixture_set_is_diverse():
    assert len(FIXTURES) >= 8
    assert {"duomo", "corvetto_barabino", "isola", "lambrate"} <= set(FIXTURES)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_counts_and_score_within_tolerance(name):
    expected, got = _both(name)
    for field in COUNT_FIELDS:
        e, g = getattr(expected, field), getattr(got, field)
        assert abs(e - g) <= max(1, 0.15 * e), f"{name}.{field}: overpass={e} bundled={g}"
    assert abs(expected.score_pct - got.score_pct) <= 0.3


def test_most_points_match_exactly():
    exact = 0
    for name in FIXTURES:
        expected, got = _both(name)
        if all(getattr(expected, f) == getattr(got, f) for f in COUNT_FIELDS):
            exact += 1
    print(f"exact count parity: {exact}/{len(FIXTURES)}")
    assert exact >= len(FIXTURES) * 2 // 3


def test_corvetto_has_the_known_transit_and_amenities():
    """Via Barabino 3 (the motivating point): same counts and map pins as Overpass."""
    expected, got = _both("corvetto_barabino")
    assert got.model_dump(exclude={"items_within_500m"}) == expected.model_dump(
        exclude={"items_within_500m"}
    )
    assert {(i.kind, i.name) for i in got.items_within_500m} == {
        (i.kind, i.name) for i in expected.items_within_500m
    }
