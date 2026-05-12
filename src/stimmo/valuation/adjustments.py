"""Hedonic adjustment coefficients applied on top of the OMI €/m² range.

This module is the single tuning surface for the valuation engine: floor,
lift, condition, energy, outdoor, box, and amenity coefficients all live
here. Do not scatter multipliers into the engine, models, or renderers.

All values are deltas in percent (e.g. -5 means -5%).
"""

from __future__ import annotations

from stimmo.models import (
    AdjustmentBreakdown,
    AmenityScore,
    ConstructionEra,
    EnergyClass,
    Exposure,
    FineCondition,
    OmiQuote,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
)

# Calibration regime: 2024–2026 Milan.  These coefficients are *structural* deltas
# on top of the current OMI band — they encode how a specific property deviates from
# the zone average.  Market-cycle effects (ECB rates, post-COVID demand shift,
# gentrification waves) are absorbed by OMI itself, which is republished each
# semester via scripts/refresh_omi.py.  Re-evaluate this file when a regime
# change occurs (e.g. an ECB pivot, EPBD enforcement milestone, or a structural
# shift in buyer preferences) — not on every market move.

# OMI publishes separate €/m² bands for OTTIMO/NORMALE/SCADENTE conservation states;
# ABITABILE maps to NORMALE (the baseline band).  NUOVO/RISTRUTTURATO deltas are
# calibrated against the typical 5–8 pp gap between OMI OTTIMO and NORMALE bands.
FINE_CONDITION_DELTA: dict[FineCondition, float] = {
    FineCondition.NUOVO: +6.0,
    FineCondition.RISTRUTTURATO: +3.0,
    FineCondition.ABITABILE: 0.0,
    # Milano renovation runs €1.2–2.0 k/m²; on 90 m² that's 15–25 % of a €700 k ask.
    FineCondition.DA_RISTRUTTURARE: -15.0,
}

# Scale calibrated against Italian green-premium research: roughly 1–2 pp per class
# in the A–D range is consistent with transaction-based studies on Italian residential
# stock.  D is the baseline because it is the most common class in Milan's existing
# stock and therefore best represents the OMI average.
ENERGY_DELTA: dict[EnergyClass, float] = {
    EnergyClass.A: +3.0,
    EnergyClass.B: +2.0,
    EnergyClass.C: +1.0,
    EnergyClass.D: 0.0,
    EnergyClass.E: -1.0,
    EnergyClass.F: -2.0,
    # Post-EPBD Directive 2024/1275/EU mandates renovation of worst-performing stock,
    # compressing demand for G-rated units beyond the linear −1 pp/class pattern.
    EnergyClass.G: -5.0,
}

# Post-COVID Milan brokerage reports (Nomisma, Tecnocasa, Scenari Immobiliari 2021–2023)
# document a structural step-up in outdoor-space valuation: balconies routinely advertised
# as primary features and large terraces commanding ≥10 % premiums in active listings.
# The Schirripa Spagnolo (2024) dataset (2004–2010) predates this shift, so the values
# here are calibrated against the current era rather than against the paper.
OUTDOOR_DELTA: dict[Outdoor, float] = {
    Outdoor.NONE: 0.0,
    Outdoor.BALCONY: +1.5,
    Outdoor.TERRACE_SMALL: +2.0,
    # Usable terrazzo >15 m² routinely fetches +10–12 % in post-2020 Milan.
    Outdoor.TERRACE_LARGE: +10.0,
}

# Pre-1950 buildings command a large positive premium in Milan hedonic data
# (+385–1623 €/m² across quantiles, Schirripa Spagnolo et al. 2024 Table 3),
# and that estimate is *net of location* — the paper's regression includes a
# bivariate thin-plate spline over UTM coordinates that absorbs spatial trends.
# The residual premium therefore reflects architectural quality and historic
# character (the authors point to "beautiful gardens inside the court", p. 176),
# not just centrality.  OMI zones are coarser than the paper's smooth spatial
# trend and likely absorb part of this premium for central zones, so the +2 %
# here is a conservative credit that avoids fully double-counting OMI while
# still acknowledging the non-location component.
# EIGHTIES_90S penalises the lower build quality of that era's rapid expansion;
# CONTEMPORARY/RECENT reward modern seismic and energy-code compliance.
CONSTRUCTION_ERA_DELTA: dict[ConstructionEra, float] = {
    ConstructionEra.PRE_WAR: +2.0,
    ConstructionEra.POSTWAR_BOOM: 0.0,
    ConstructionEra.EIGHTIES_90S: -3.0,
    ConstructionEra.CONTEMPORARY: +2.0,
    ConstructionEra.RECENT: +4.0,
}

# Calibrated judgement calls for Milan (45°N).  The north-facing penalty exceeds the
# south premium because full north exposure means negligible direct sunlight year-round
# rather than just "less sun" — an asymmetric quality gap, not a symmetric scale.
ORIENTATION_DELTA: dict[Orientation, float] = {
    Orientation.SOUTH: +4.0,
    Orientation.MIXED: 0.0,
    Orientation.NORTH: -6.0,
}

# Quiet courtyard = less noise/more privacy; mixed = partial benefit; street = baseline.
EXPOSURE_DELTA: dict[Exposure, float] = {
    Exposure.STREET: 0.0,
    Exposure.MIXED: +0.5,
    Exposure.INTERNAL_COURTYARD: +1.5,
}

# Parking adds a significant positive premium in Milan across all price quantiles
# (+1228–2325 €/m², Schirripa Spagnolo et al. 2024 Table 3).  A flat € amount is more
# appropriate than a percentage because garage value is zone-priced independently of
# dwelling size; the OMI-mid × 18 m² formula proxies the zone's garage market value.
BOX_SURFACE_EQUIV_M2 = 18
BOX_EUR_MIN = 15_000.0
BOX_EUR_MAX = 60_000.0

# Lift is consistently a substantial positive premium across all price quantiles
# (+532–862 €/m², Schirripa Spagnolo et al. 2024 Table 3 — roughly +9–14 % on the
# median baseline).  Applied only to non-SIGNORILI properties: the OMI SIGNORILI
# band already prices in near-universal lift presence for that segment.  The +2 %
# magnitude is intentionally conservative — `_floor_delta` already encodes the
# no-lift downside conditionally (1st-floor and 3+-floor penalties), so this is
# the residual credit for mid-floor units that the floor logic does not reward.
LIFT_DELTA = +2.0

# Total multiplier is clamped to this band to avoid runaway combinations.
MIN_MULTIPLIER = 0.70
MAX_MULTIPLIER = 1.30


def _room_density_delta(p: Property) -> float:
    # Penalises cramped layouts (e.g. a 3-locale at 54 m² = 18 m²/room).  The
    # thresholds (22/18 m²/room) and magnitudes are calibrated judgement calls.
    if p.room_count is None or p.room_count < 3:
        return 0.0
    m2_per_locale = p.surface_m2 / p.room_count
    if m2_per_locale >= 22:
        return 0.0
    if m2_per_locale >= 18:
        return -2.0
    return -4.0


def _floor_delta(p: Property) -> float:
    # Schirripa Spagnolo et al. (2024) find both ground/1st-floor and 4th+-floor units
    # penalised vs. the 2nd–3rd-floor baseline in Milan (Table 3), with significance
    # fading at higher price quantiles.  The lift-conditioned branches below are not in
    # the paper but reflect standard Italian appraisal practice.
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
    # OMI bands are published per zone/type/condition only — no size dimension — so they
    # represent a transaction-weighted average that does not account for size effects.
    # The size adjustment compensates at both tails of the area distribution:
    #
    # Small units (<35 m²): monolocali and bilocali consistently trade at a per-m²
    # premium in Milan because the OMI band averages over a size mix dominated by
    # larger flats.  The +5 % bonus reflects this systematic per-m² premium for
    # studio-sized units.
    #
    # Large units (>180 m²): Schirripa Spagnolo et al. (2024) model the Milan
    # apartment market using a cubic polynomial on housing area and find a non-linear
    # area-price relationship: for above-median priced properties the curve becomes
    # concave for larger units, meaning "buyers of more luxurious units attribute a
    # progressively lower value to the house size" (Fig. 5, Table 3).  The inflection
    # point for q=0.75 falls at x≈0.71 on the standardised [0,1] scale.  The paper
    # does not publish the housing-area range, so the precise mapping to m² is
    # speculative — 180 m² is a calibrated judgement consistent with the upper end
    # of the Milan apartment market.  The −5 % magnitude is similarly a judgement
    # call rather than a direct paper read.
    # Sources:
    #   OMI methodology — https://www.agenziaentrate.gov.it/portale/schede/fabbricatiterreni/omi/banche-dati/quotazioni-immobiliari
    #   Schirripa Spagnolo et al. (2024), AStA Adv. Stat. Anal. — https://doi.org/10.1007/s10182-023-00476-w
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

    if p.has_lift and p.property_type != PropertyType.SIGNORILI:
        deltas.append(
            AdjustmentBreakdown(
                code="lift",
                params={},
                delta_pct=LIFT_DELTA,
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

    exp = EXPOSURE_DELTA[p.exposure]
    if exp:
        deltas.append(
            AdjustmentBreakdown(
                code="exposure",
                params={"exposure": p.exposure.value},
                delta_pct=exp,
            )
        )

    rd = _room_density_delta(p)
    if rd:
        m2_per_locale = p.surface_m2 / p.room_count  # type: ignore[operator]
        deltas.append(
            AdjustmentBreakdown(
                code="room_density",
                params={
                    "rooms": p.room_count,
                    "m2_per_locale": round(m2_per_locale, 1),
                },
                delta_pct=rd,
            )
        )

    # Second bathroom is significant and stable across quantiles in Milan (+743–925 €/m²,
    # Schirripa Spagnolo et al. 2024 Table 3); +5% and the ≥75 m² guard are calibrated
    # conservatively — at high price-per-m² the relative premium is smaller.
    if p.has_second_bathroom and p.surface_m2 >= 75:
        deltas.append(AdjustmentBreakdown(code="second_bathroom", params={}, delta_pct=+5.0))

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
