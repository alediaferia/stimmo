from __future__ import annotations

import pytest

from stimmo.models import (
    AmenityScore,
    ConstructionEra,
    FineCondition,
    OmiCondition,
    OmiQuote,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
)
from stimmo.valuation import adjustments, engine, verdict


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


def _prop(**kw) -> Property:
    base = dict(
        address="Via X",
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
        asking_price_eur=650_000,
    )
    base.update(kw)
    return Property(**base)


def test_fair_in_range(quote):
    p = _prop(asking_price_eur=650_000)  # mid ≈ 8150 * 80 = 652_000
    est = engine.estimate(p, quote, AmenityScore())
    assert est.verdict == "fair"
    assert est.range_low_eur < est.range_high_eur


def test_over_priced(quote):
    p = _prop(asking_price_eur=1_500_000)
    est = engine.estimate(p, quote, AmenityScore())
    assert est.verdict == "over"
    assert est.asking_vs_ask_mid_pct > 0


def test_under_priced(quote):
    p = _prop(asking_price_eur=200_000)
    est = engine.estimate(p, quote, AmenityScore())
    assert est.verdict == "under"


def test_ask_premium_pct_is_six():
    assert verdict.ASK_PREMIUM_PCT == 6.0


def test_ask_premium_shifts_under_threshold_up(quote):
    # rogito range ≈ 592_000–712_000; ask_low * 0.95 ≈ 627_520 * 0.95 ≈ 596_144
    # 580_000 < 596_144 → "under" (was "fair" before the premium shift)
    p = _prop(asking_price_eur=580_000)
    est = engine.estimate(p, quote, AmenityScore())
    assert est.verdict == "under"


def test_ask_premium_widens_fair_ceiling(quote):
    # rogito high ≈ 712_000; ask_high * 1.05 ≈ 754_720 * 1.05 ≈ 792_456
    # 780_000 < 792_456 → "fair" (was "over" before the premium shift)
    p = _prop(asking_price_eur=780_000)
    est = engine.estimate(p, quote, AmenityScore())
    assert est.verdict == "fair"


def test_ask_range_fields_match_premium(quote):
    est = engine.estimate(_prop(), quote, AmenityScore())
    factor = 1 + verdict.ASK_PREMIUM_PCT / 100
    assert est.ask_range_low_eur == pytest.approx(est.range_low_eur * factor, rel=1e-6)
    assert est.ask_range_mid_eur == pytest.approx(est.range_mid_eur * factor, rel=1e-6)
    assert est.ask_range_high_eur == pytest.approx(est.range_high_eur * factor, rel=1e-6)


def test_box_shifts_range_up(quote):
    base_est = engine.estimate(_prop(), quote, AmenityScore())
    box_est = engine.estimate(_prop(has_box=True), quote, AmenityScore())
    zone_mid = (quote.eur_m2_min + quote.eur_m2_max) / 2
    expected_box_eur = max(
        adjustments.BOX_EUR_MIN,
        min(adjustments.BOX_EUR_MAX, zone_mid * adjustments.BOX_SURFACE_EQUIV_M2),
    )
    assert box_est.range_mid_eur == pytest.approx(
        base_est.range_mid_eur + expected_box_eur, rel=1e-6
    )
