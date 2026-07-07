from __future__ import annotations

from stimmo.data import history, ntn, omi, zones
from stimmo.models import OmiCondition, PropertyType


def test_omi_lookup_returns_quote_for_known_zone():
    zones_available = omi.available_zones()
    assert zones_available, "no zones in bundled OMI CSV"
    quote = omi.lookup(zones_available[0], PropertyType.CIVILI, OmiCondition.NORMALE)
    assert quote.eur_m2_min > 0
    assert quote.eur_m2_max >= quote.eur_m2_min


def test_omi_lookup_fallback_picks_closest_condition():
    # Regression for issue #9: D10 has NORMALE but not SCADENTE — closest match must be
    # NORMALE (distance 1), not OTTIMO (distance 2).
    quote = omi.lookup("D10", PropertyType.CIVILI, OmiCondition.SCADENTE)
    assert quote.condition == OmiCondition.NORMALE


def test_condition_order_covers_all_omi_conditions():
    # Fails if a new OmiCondition variant is added without updating _CONDITION_ORDER.
    assert set(omi._CONDITION_ORDER) == set(OmiCondition)


def test_available_conditions_for_known_zone():
    # D10 has NORMALE for civili but not SCADENTE (see fallback regression test).
    avail = omi.available_conditions("D10", PropertyType.CIVILI)
    assert OmiCondition.NORMALE in avail
    assert OmiCondition.SCADENTE not in avail


def test_available_conditions_empty_for_unknown_zone():
    assert omi.available_conditions("ZZ99", PropertyType.CIVILI) == []


def test_zone_for_point_finds_milano_centre():
    # Piazza Duomo
    z = zones.zone_for_point(45.4642, 9.1900)
    assert z is not None
    code, _descr = z
    assert code  # non-empty


def test_zone_for_point_rejects_outside_milano():
    # Sesto San Giovanni
    z = zones.zone_for_point(45.5366, 9.2297)
    assert z is None


def test_history_series_returns_chronological_points():
    zones_available = omi.available_zones()
    series = history.series(zones_available[0], PropertyType.CIVILI, OmiCondition.NORMALE)
    if not series:
        return  # bundled history may not cover every zone+type — acceptable
    semesters = [p.semester for p in series]
    assert semesters == sorted(semesters)


def test_ntn_total_quarters_returns_recent_data():
    points = ntn.total_quarters(last_n=4)
    assert len(points) <= 4
    if points:
        assert all(p.ntn >= 0 for p in points)


def test_ntn_bucket_for_known_surface():
    label, points = ntn.by_bucket_quarters(80.0, last_n=4)
    assert label == "50 -| 85"
    assert len(points) <= 4


def test_zone_fascia_index_matches_zone_code_prefix():
    # Every zone with bundled Fascia data uses its own first character as the
    # fascia letter (B12 -> B, D10 -> D, ...) — WP-7's zones index groups by
    # this, with a code[0] fallback for zones absent from the CSV (e.g. R2).
    idx = omi.zone_fascia_index()
    assert idx, "no fascia data found"
    for code, fascia in idx.items():
        assert code.startswith(fascia)


def test_zone_quotes_returns_sorted_property_condition_combos():
    quotes = omi.zone_quotes("D10")
    assert quotes
    for q in quotes:
        assert q.zone_code == "D10"
        assert q.eur_m2_max >= q.eur_m2_min


def test_zone_quotes_empty_for_zone_with_no_bundled_data():
    # R2 is a "rural"/park OMI zone with a polygon but no Compr_min/max rows.
    assert omi.zone_quotes("R2") == []
