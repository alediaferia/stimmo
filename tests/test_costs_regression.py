"""The purchase-cost calculator must not move the valuation by a single euro.

The golden numbers below were captured from `engine.estimate` before `stimmo.costs` existed.
If one of them changes, a valuation coefficient (or the engine) changed — that belongs in
`valuation/adjustments.py` and its own tests, never as a side effect of the cost rates.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from stimmo.costs import rates
from stimmo.models import (
    AmenityScore,
    ConstructionEra,
    EnergyClass,
    Estimate,
    FineCondition,
    OmiCondition,
    OmiQuote,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
    RenovationTier,
)
from stimmo.valuation import adjustments, engine

_NORMALE = OmiQuote(
    zone_code="B12",
    property_type=PropertyType.CIVILI,
    condition=OmiCondition.NORMALE,
    eur_m2_min=7400,
    eur_m2_max=8900,
    semester="2018-2",
)
_SCADENTE = OmiQuote(
    zone_code="B12",
    property_type=PropertyType.CIVILI,
    condition=OmiCondition.SCADENTE,
    eur_m2_min=4200,
    eur_m2_max=5600,
    semester="2018-2",
)


def _prop(**kw) -> Property:
    base = dict(
        address="Via X",
        surface_m2=70,
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
        asking_price_eur=450_000,
    )
    base.update(kw)
    return Property(**base)


# name -> (property, quote, multiplier, flat, low, mid, high, ask_mid, verdict, vs_ask_mid, deltas)
_GOLDEN = {
    "abitabile": (
        _prop(),
        _NORMALE,
        1.02, 0.0, 528_360.0, 581_910.0, 635_460.0, 616_824.6, "under", -27.04571121190692,
        {"lift": 2.0},
    ),
    "da_ristrutturare": (
        _prop(
            fine_condition=FineCondition.DA_RISTRUTTURARE,
            omi_condition=OmiCondition.SCADENTE,
            asking_price_eur=300_000,
        ),
        _SCADENTE,
        0.87, 0.0, 255_780.0, 298_410.0, 341_040.0, 316_314.6, "fair", -5.1577132386554485,
        {"lift": 2.0, "condition_fine": -15.0},
    ),
    "nuovo_box_terrazzo": (
        _prop(
            fine_condition=FineCondition.NUOVO,
            has_box=True,
            outdoor=Outdoor.TERRACE_LARGE,
            energy_class=EnergyClass.A,
        ),
        _NORMALE,
        1.21, 60_000.0, 686_780.0, 750_305.0, 813_830.0, 795_323.3, "under", -43.419235925817844,
        {"lift": 2.0, "condition_fine": 6.0, "energy_class": 3.0, "outdoor": 10.0},
    ),
    "ristrutturato": (
        _prop(fine_condition=FineCondition.RISTRUTTURATO, surface_m2=100, asking_price_eur=800_000),
        _NORMALE,
        1.05, 0.0, 777_000.0, 855_750.0, 934_500.0, 907_095.0, "fair", -11.806370887283036,
        {"lift": 2.0, "condition_fine": 3.0},
    ),
}  # fmt: skip


def _check(name: str, est: Estimate) -> None:
    _, _, mult, flat, low, mid, high, ask_mid, verdict, vs_ask, deltas = _GOLDEN[name]
    assert est.multiplier == pytest.approx(mult, abs=1e-12)
    assert est.flat_extras_eur == pytest.approx(flat)
    assert est.range_low_eur == pytest.approx(low)
    assert est.range_mid_eur == pytest.approx(mid)
    assert est.range_high_eur == pytest.approx(high)
    assert est.ask_range_mid_eur == pytest.approx(ask_mid)
    assert est.verdict == verdict
    assert est.asking_vs_ask_mid_pct == pytest.approx(vs_ask)
    assert {b.code: b.delta_pct for b in est.breakdown} == deltas


@pytest.mark.parametrize("name", list(_GOLDEN))
def test_engine_estimate_is_unchanged(name):
    prop, quote = _GOLDEN[name][:2]
    _check(name, engine.estimate(prop, quote, AmenityScore()))


@pytest.mark.parametrize("name", list(_GOLDEN))
def test_estimate_does_not_depend_on_any_cost_rate(name, monkeypatch):
    """Rewrite every renovation rate to nonsense: the valuation must not notice."""
    monkeypatch.setattr(
        rates,
        "RENOVATION_TIERS",
        {tier: rates.RenovationTierRate(1, 2) for tier in rates.RENOVATION_TIERS},
    )
    monkeypatch.setattr(
        rates,
        "FINE_CONDITION_DEFAULT_TIER",
        dict.fromkeys(FineCondition, RenovationTier.PREMIUM),
    )
    prop, quote = _GOLDEN[name][:2]
    _check(name, engine.estimate(prop, quote, AmenityScore()))


@pytest.mark.parametrize("name", list(_GOLDEN))
def test_estimate_does_not_depend_on_the_ltv_rules(name, monkeypatch):
    """The mortgage LTV limits and the Consap rules feed purchase-cost notes only."""
    for const in (
        "MUTUO_FONDIARIO_MAX_LTV_PCT",
        "MUTUO_FONDIARIO_MAX_LTV_WITH_GUARANTEES_PCT",
        "CONSAP_GUARANTEE_PCT",
        "CONSAP_MAX_LOAN_EUR",
        "CONSAP_ENHANCED_MIN_LTV_PCT",
    ):
        monkeypatch.setattr(rates, const, 1.0)
    prop, quote = _GOLDEN[name][:2]
    _check(name, engine.estimate(prop, quote, AmenityScore()))


@pytest.mark.parametrize("name", list(_GOLDEN))
def test_estimate_does_not_depend_on_any_review_date(name, monkeypatch):
    """Make every benchmark ancient, so every cost range is overdue: the valuation must not
    notice. Staleness is a property of purchase-cost lines, never of the estimate."""
    ancient = date(2000, 1, 1)
    for const, value in list(vars(rates).items()):
        if isinstance(value, rates.Benchmark):
            monkeypatch.setattr(rates, const, replace(value, as_of=ancient))
        elif const in ("NOTARY_PURCHASE_BANDS", "NOTARY_MORTGAGE_BANDS"):
            bands = tuple(
                replace(band, benchmark=replace(band.benchmark, as_of=ancient)) for band in value
            )
            monkeypatch.setattr(rates, const, bands)
    monkeypatch.setattr(
        rates, "RENOVATION_TIERS_REVIEW", replace(rates.RENOVATION_TIERS_REVIEW, as_of=ancient)
    )
    # ...and the statutory block's yearly reminder too.
    monkeypatch.setattr(rates, "STATUTORY_AS_OF", ancient)
    assert all(item.overdue(date(2026, 10, 8)) for item in rates.review_schedule())
    prop, quote = _GOLDEN[name][:2]
    _check(name, engine.estimate(prop, quote, AmenityScore()))


def test_estimate_has_no_notion_of_the_mortgage_payment_or_its_reference_rates():
    """The payment block and the Banca d'Italia reference rates live in the cost calculator
    only: nothing about them can reach the estimate, the verdict or the adjustments."""
    assert not {"mortgage_payment", "monthly_payment_eur", "mortgage_rate_pct"} & set(
        Estimate.model_fields
    )
    for module in (adjustments, engine):
        source = Path(module.__file__).read_text()
        assert "MORTGAGE" not in source and "mortgage" not in source.lower()


def test_estimate_has_no_notion_of_stale_ranges():
    assert not {"stale", "due", "as_of"} & set(Estimate.model_fields)


def test_condition_delta_is_still_the_valuation_judgement():
    assert adjustments.FINE_CONDITION_DELTA == {
        FineCondition.NUOVO: 6.0,
        FineCondition.RISTRUTTURATO: 3.0,
        FineCondition.ABITABILE: 0.0,
        FineCondition.DA_RISTRUTTURARE: -15.0,
    }


def test_estimate_model_shape_is_untouched():
    assert set(Estimate.model_fields) == {
        "base_eur_m2_min",
        "base_eur_m2_max",
        "multiplier",
        "flat_extras_eur",
        "adjusted_eur_m2_min",
        "adjusted_eur_m2_max",
        "adjusted_eur_m2_mid",
        "range_low_eur",
        "range_mid_eur",
        "range_high_eur",
        "ask_premium_pct",
        "ask_range_low_eur",
        "ask_range_mid_eur",
        "ask_range_high_eur",
        "breakdown",
        "omi_quote",
        "amenity_score",
        "verdict",
        "asking_vs_ask_mid_pct",
    }


@pytest.mark.parametrize("module", ["adjustments", "engine", "verdict"])
def test_valuation_modules_do_not_import_the_costs_package(module):
    path = Path(adjustments.__file__).with_name(f"{module}.py")
    imported: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [m for m in imported if m.startswith("stimmo.costs")]


def test_costs_package_does_not_import_the_valuation_package():
    costs_dir = Path(rates.__file__).parent
    for path in costs_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not [n for n in names if n.startswith("stimmo.valuation")], path.name
