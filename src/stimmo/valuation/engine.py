from __future__ import annotations

from stimmo.models import AmenityScore, Estimate, OmiQuote, Property
from stimmo.valuation import adjustments, verdict


def estimate(p: Property, quote: OmiQuote, amenity: AmenityScore) -> Estimate:
    mult, flat, breakdown = adjustments.compute(p, amenity, quote)

    adj_min = quote.eur_m2_min * mult
    adj_max = quote.eur_m2_max * mult
    adj_mid = (adj_min + adj_max) / 2

    low = adj_min * p.surface_m2 + flat
    high = adj_max * p.surface_m2 + flat
    mid = adj_mid * p.surface_m2 + flat

    factor = 1 + verdict.ASK_PREMIUM_PCT / 100
    ask_low = low * factor
    ask_mid = mid * factor
    ask_high = high * factor

    v = verdict.classify(p.asking_price_eur, low, high)
    asking_vs_ask_mid = (p.asking_price_eur / ask_mid - 1) * 100

    return Estimate(
        base_eur_m2_min=quote.eur_m2_min,
        base_eur_m2_max=quote.eur_m2_max,
        multiplier=mult,
        flat_extras_eur=flat,
        adjusted_eur_m2_min=adj_min,
        adjusted_eur_m2_max=adj_max,
        adjusted_eur_m2_mid=adj_mid,
        range_low_eur=low,
        range_mid_eur=mid,
        range_high_eur=high,
        ask_premium_pct=verdict.ASK_PREMIUM_PCT,
        ask_range_low_eur=ask_low,
        ask_range_mid_eur=ask_mid,
        ask_range_high_eur=ask_high,
        breakdown=breakdown,
        omi_quote=quote,
        amenity_score=amenity,
        verdict=v,
        asking_vs_ask_mid_pct=asking_vs_ask_mid,
    )
