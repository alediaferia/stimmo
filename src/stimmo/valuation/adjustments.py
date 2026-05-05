"""Hedonic adjustment coefficients applied on top of the OMI €/m² range.

All values are deltas in percent (e.g. -5 means -5%). Tune in one place.
"""

from __future__ import annotations

from stimmo.models import (
    AdjustmentBreakdown,
    AmenityScore,
    ConstructionEra,
    EnergyClass,
    FineCondition,
    OmiQuote,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
)

FINE_CONDITION_DELTA: dict[FineCondition, float] = {
    FineCondition.NUOVO: +6.0,
    FineCondition.RISTRUTTURATO: +3.0,
    FineCondition.ABITABILE: 0.0,
    # Milano renovation runs €1.2–2.0 k/m²; on 90 m² that's 15–25 % of a €700 k ask.
    FineCondition.DA_RISTRUTTURARE: -15.0,
}

ENERGY_DELTA: dict[EnergyClass, float] = {
    EnergyClass.A: +3.0,
    EnergyClass.B: +2.0,
    EnergyClass.C: +1.0,
    EnergyClass.D: 0.0,
    EnergyClass.E: -1.0,
    EnergyClass.F: -2.0,
    # Post-EPBD (2024) G-class tail prices worse than linear.
    EnergyClass.G: -5.0,
}

OUTDOOR_DELTA: dict[Outdoor, float] = {
    Outdoor.NONE: 0.0,
    Outdoor.BALCONY: +1.0,
    Outdoor.TERRACE_SMALL: +2.0,
    # Usable terrazzo >15 m² routinely fetches +8–12 % in Milano.
    Outdoor.TERRACE_LARGE: +8.0,
}

CONSTRUCTION_ERA_DELTA: dict[ConstructionEra, float] = {
    ConstructionEra.PRE_WAR: 0.0,
    ConstructionEra.POSTWAR_BOOM: 0.0,
    ConstructionEra.EIGHTIES_90S: -3.0,
    ConstructionEra.CONTEMPORARY: +2.0,
    ConstructionEra.RECENT: +4.0,
}

ORIENTATION_DELTA: dict[Orientation, float] = {
    Orientation.SOUTH: +4.0,
    Orientation.MIXED: 0.0,
    Orientation.NORTH: -6.0,
}

# Box auto value is zone-relative: 18 m² equivalent on the OMI mid price,
# clamped to a realistic range across Milano zones.
BOX_SURFACE_EQUIV_M2 = 18
BOX_EUR_MIN = 15_000.0
BOX_EUR_MAX = 60_000.0

# Total multiplier is clamped to this band to avoid runaway combinations.
MIN_MULTIPLIER = 0.70
MAX_MULTIPLIER = 1.30


def _floor_delta(p: Property) -> float:
    if p.floor <= 0:
        # Signorili rialzato is barely penalised; Civili/Economico ground floor much more.
        return -3.0 if p.property_type == PropertyType.SIGNORILI else -10.0
    if p.floor == 1 and not p.has_lift:
        return -4.0
    if not p.has_lift and p.floor >= 3:
        return -8.0
    if p.has_lift and p.floor >= max(3, p.total_floors - 1):
        return +3.0
    if p.floor == p.total_floors and not p.has_lift:
        return -3.0
    return 0.0


def _size_delta(p: Property) -> float:
    if p.surface_m2 < 35:
        return +5.0
    if p.surface_m2 > 180:
        return -5.0
    return 0.0


def compute(
    p: Property, amenity: AmenityScore, omi_quote: OmiQuote
) -> tuple[float, float, list[AdjustmentBreakdown]]:
    """Return (multiplier, flat_extras_eur, breakdown).

    Multiplier is built additively in pct space then converted: 1 + sum(deltas)/100,
    clamped to [MIN_MULTIPLIER, MAX_MULTIPLIER]. Flat extras (e.g. box auto) are
    added in € after the €/m² × surface step.
    """
    deltas: list[AdjustmentBreakdown] = []

    floor = _floor_delta(p)
    if floor:
        deltas.append(
            AdjustmentBreakdown(
                code="floor",
                params={"floor": p.floor, "lift": p.has_lift},
                delta_pct=floor,
            )
        )

    size = _size_delta(p)
    if size:
        deltas.append(
            AdjustmentBreakdown(
                code="size",
                params={"surface_m2": p.surface_m2},
                delta_pct=size,
            )
        )

    fine = FINE_CONDITION_DELTA[p.fine_condition]
    if fine:
        deltas.append(
            AdjustmentBreakdown(
                code="condition_fine",
                params={"condition": p.fine_condition.value},
                delta_pct=fine,
            )
        )

    if p.energy_class is not None:
        en = ENERGY_DELTA[p.energy_class]
        if en:
            deltas.append(
                AdjustmentBreakdown(
                    code="energy_class",
                    params={"cls": p.energy_class.value},
                    delta_pct=en,
                )
            )

    out = OUTDOOR_DELTA[p.outdoor]
    if out:
        deltas.append(
            AdjustmentBreakdown(
                code="outdoor",
                params={"outdoor": p.outdoor.value},
                delta_pct=out,
            )
        )

    era = CONSTRUCTION_ERA_DELTA[p.construction_era]
    if era:
        deltas.append(
            AdjustmentBreakdown(
                code="construction_era",
                params={"era": p.construction_era.value},
                delta_pct=era,
            )
        )

    ori = ORIENTATION_DELTA[p.orientation]
    if ori:
        deltas.append(
            AdjustmentBreakdown(
                code="orientation",
                params={"orientation": p.orientation.value},
                delta_pct=ori,
            )
        )

    if p.has_second_bathroom and p.surface_m2 >= 75:
        deltas.append(
            AdjustmentBreakdown(code="second_bathroom", params={}, delta_pct=+5.0)
        )

    if amenity.score_pct:
        deltas.append(
            AdjustmentBreakdown(
                code="amenities",
                params={"score_pct": amenity.score_pct},
                delta_pct=amenity.score_pct,
            )
        )

    pct = sum(d.delta_pct for d in deltas)
    raw_mult = 1 + pct / 100
    mult = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, raw_mult))
    if mult != raw_mult:
        deltas.append(
            AdjustmentBreakdown(
                code="clamp",
                params={"raw": raw_mult, "clamped": mult},
                delta_pct=(mult - raw_mult) * 100,
            )
        )

    if p.has_box:
        zone_mid = (omi_quote.eur_m2_min + omi_quote.eur_m2_max) / 2
        flat = max(BOX_EUR_MIN, min(BOX_EUR_MAX, zone_mid * BOX_SURFACE_EQUIV_M2))
    else:
        flat = 0.0
    return mult, flat, deltas
