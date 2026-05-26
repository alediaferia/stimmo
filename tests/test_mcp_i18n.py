"""i18n tests for MCP tools and resources."""

from __future__ import annotations

import json

from stimmo.data import amenities, geocode, zones
from stimmo.mcp import resources as _res
from stimmo.mcp.tools import estimate_property, omi_history, omi_zone_for_point
from stimmo.models import AmenityScore, OmiCondition, PropertyType

# ── Resource locale tests ──────────────────────────────────────────────────


def test_property_type_vocab_italian():
    text = _res.property_type_vocab()
    vocab = json.loads(text)
    # Italian hints come from gettext_lazy strings
    civili = next(v for v in vocab if v["value"] == "Abitazioni civili")
    assert len(civili["hint"]) > 0


def test_outdoor_vocab_returns_all_values():
    text = _res.outdoor_vocab()
    vocab = json.loads(text)
    values = {item["value"] for item in vocab}
    assert {"none", "balcony", "terrace_small", "terrace_large"} == values


def test_construction_era_vocab_returns_all_values():
    text = _res.construction_era_vocab()
    vocab = json.loads(text)
    values = {item["value"] for item in vocab}
    assert {"pre_war", "postwar_boom", "eighties_90s", "contemporary", "recent"} == values


# ── Tool locale tests ──────────────────────────────────────────────────────


async def test_estimate_property_lang_en(monkeypatch):
    """breakdown codes are language-neutral; no exception on lang=en."""
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    result = await estimate_property(
        address="Piazza Duomo, Milano",
        surface_m2=80.0,
        property_type=PropertyType.CIVILI,
        fine_condition="abitabile",  # type: ignore[arg-type]
        floor=2,
        total_floors=5,
        has_lift=True,
        asking_price_eur=650000.0,
        construction_era="postwar_boom",  # type: ignore[arg-type]
        lang="en",
    )

    assert result["verdict"] in ("under", "fair", "over")
    # breakdown codes are language-neutral identifiers
    for entry in result["breakdown"]:
        assert isinstance(entry["code"], str)
        assert len(entry["code"]) > 0


async def test_estimate_property_lang_fr_falls_back(monkeypatch):
    """Unsupported lang falls back to Italian without error."""
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    result = await estimate_property(
        address="Piazza Duomo, Milano",
        surface_m2=80.0,
        property_type=PropertyType.CIVILI,
        fine_condition="abitabile",  # type: ignore[arg-type]
        floor=2,
        total_floors=5,
        has_lift=True,
        asking_price_eur=650000.0,
        construction_era="postwar_boom",  # type: ignore[arg-type]
        lang="fr",  # type: ignore[arg-type]
    )

    assert result["verdict"] in ("under", "fair", "over")


async def test_omi_zone_for_point_succeeds(monkeypatch):
    monkeypatch.setattr(zones, "zone_for_point", lambda lat, lon: ("B1", "Centro Storico"))
    result = await omi_zone_for_point(lat=45.4642, lon=9.1900, lang="en")
    assert result["zone_code"] == "B1"


async def test_omi_history_returns_series():
    points = await omi_history(
        zone_code="B1",
        property_type=PropertyType.CIVILI,
        condition=OmiCondition.NORMALE,
    )
    # Might be empty if B1 doesn't exist in bundled data; just check types
    for p in points:
        assert "semester" in p
        assert p["eur_m2_min"] > 0
