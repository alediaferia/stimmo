from __future__ import annotations

import pytest

from stimmo.models import (
    AmenityScore,
    ConstructionEra,
    EnergyClass,
    FineCondition,
    OmiCondition,
    OmiQuote,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
)
from stimmo.valuation import adjustments


@pytest.fixture
def quote() -> OmiQuote:
    return OmiQuote(
        zone_code="B12",
        property_type=PropertyType.CIVILI,
        condition=OmiCondition.NORMALE,
        eur_m2_min=7400,
        eur_m2_max=8900,
        semester="2018-2",
    )


def _base(**overrides) -> Property:
    base = dict(
        address="Via Test 1",
        surface_m2=80,
        property_type=PropertyType.CIVILI,
        omi_condition=OmiCondition.NORMALE,
        fine_condition=FineCondition.ABITABILE,
        floor=2,
        total_floors=5,
        has_lift=True,
        outdoor=Outdoor.NONE,
        has_box=False,
        construction_era=ConstructionEra.POSTWAR_BOOM,
        orientation=Orientation.MIXED,
        asking_price_eur=400_000,
    )
    base.update(overrides)
    return Property(**base)


def test_no_adjustments_yields_unit_multiplier(quote):
    p = _base()
    mult, flat, deltas = adjustments.compute(p, AmenityScore(), quote)
    assert mult == 1.0
    assert flat == 0
    assert deltas == []


def test_box_adds_flat_extra(quote):
    p = _base(has_box=True)
    _, flat, _ = adjustments.compute(p, AmenityScore(), quote)
    zone_mid = (quote.eur_m2_min + quote.eur_m2_max) / 2
    box_val = zone_mid * adjustments.BOX_SURFACE_EQUIV_M2
    expected = max(adjustments.BOX_EUR_MIN, min(adjustments.BOX_EUR_MAX, box_val))
    assert flat == pytest.approx(expected)


def test_da_ristrutturare_lowers_multiplier(quote):
    p = _base(fine_condition=FineCondition.DA_RISTRUTTURARE)
    mult, _, _ = adjustments.compute(p, AmenityScore(), quote)
    assert mult < 1.0
    assert mult >= adjustments.MIN_MULTIPLIER


def test_da_ristrutturare_penalty_is_15pct():
    assert adjustments.FINE_CONDITION_DELTA[FineCondition.DA_RISTRUTTURARE] == -15.0


def test_terrace_large_premium_is_8pct():
    assert adjustments.OUTDOOR_DELTA[Outdoor.TERRACE_LARGE] == 8.0


def test_energy_g_penalty_is_5pct():
    from stimmo.models import EnergyClass

    assert adjustments.ENERGY_DELTA[EnergyClass.G] == -5.0


def test_floor_no_lift_high_penalty(quote):
    p = _base(floor=4, has_lift=False)
    _, _, deltas = adjustments.compute(p, AmenityScore(), quote)
    floor_delta = next(d.delta_pct for d in deltas if d.code == "floor")
    assert floor_delta == -8.0


def test_ground_floor_civili_penalty(quote):
    p = _base(floor=0)
    _, _, deltas = adjustments.compute(p, AmenityScore(), quote)
    floor_delta = next(d.delta_pct for d in deltas if d.code == "floor")
    assert floor_delta == -10.0


def test_ground_floor_signorili_penalty(quote):
    from stimmo.models import PropertyType

    p = _base(floor=0, property_type=PropertyType.SIGNORILI)
    _, _, deltas = adjustments.compute(p, AmenityScore(), quote)
    floor_delta = next(d.delta_pct for d in deltas if d.code == "floor")
    assert floor_delta == -3.0


def test_clamping_extreme_negative(quote):
    p = _base(
        fine_condition=FineCondition.DA_RISTRUTTURARE,
        floor=0,
        has_lift=False,
        surface_m2=200,
        energy_class=EnergyClass.G,
    )
    mult, _, _ = adjustments.compute(p, AmenityScore(), quote)
    assert mult >= adjustments.MIN_MULTIPLIER


def test_clamping_extreme_positive(quote):
    p = _base(
        fine_condition=FineCondition.NUOVO,
        floor=8,
        total_floors=8,
        has_lift=True,
        surface_m2=30,
        energy_class=EnergyClass.A,
        outdoor=Outdoor.TERRACE_LARGE,
    )
    amen = AmenityScore(score_pct=5.0)
    mult, _, _ = adjustments.compute(p, amen, quote)
    assert mult <= adjustments.MAX_MULTIPLIER


def test_amenities_increase_multiplier(quote):
    p = _base()
    mult_no = adjustments.compute(p, AmenityScore(), quote)[0]
    mult_yes = adjustments.compute(p, AmenityScore(score_pct=4.0), quote)[0]
    assert mult_yes > mult_no


def test_construction_era_eighties_penalty():
    assert adjustments.CONSTRUCTION_ERA_DELTA[ConstructionEra.EIGHTIES_90S] == -3.0


def test_orientation_north_penalty():
    assert adjustments.ORIENTATION_DELTA[Orientation.NORTH] == -6.0


def test_orientation_south_premium():
    assert adjustments.ORIENTATION_DELTA[Orientation.SOUTH] == +4.0


def test_second_bathroom_applies_above_75m2(quote):
    p = _base(has_second_bathroom=True, surface_m2=80)
    _, _, deltas = adjustments.compute(p, AmenityScore(), quote)
    second_bath = next((d for d in deltas if d.code == "second_bathroom"), None)
    assert second_bath is not None
    assert second_bath.delta_pct == +5.0


def test_second_bathroom_ignored_below_75m2(quote):
    p = _base(has_second_bathroom=True, surface_m2=60)
    _, _, deltas = adjustments.compute(p, AmenityScore(), quote)
    assert not any(d.code == "second_bathroom" for d in deltas)


def test_box_value_is_zone_relative(quote):
    p = _base(has_box=True)
    _, flat, _ = adjustments.compute(p, AmenityScore(), quote)
    zone_mid = (quote.eur_m2_min + quote.eur_m2_max) / 2
    box_val = zone_mid * adjustments.BOX_SURFACE_EQUIV_M2
    expected = max(adjustments.BOX_EUR_MIN, min(adjustments.BOX_EUR_MAX, box_val))
    assert flat == pytest.approx(expected)
