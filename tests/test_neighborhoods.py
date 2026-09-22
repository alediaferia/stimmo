from __future__ import annotations

import re

from stimmo.data import neighborhoods, zones

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_zone_codes_are_real_omi_zones():
    known = {code for code, _descr in zones.list_zones()}
    for n in neighborhoods.list_neighborhoods():
        for code in n.zone_codes:
            assert code in known, f"{n.slug_en} references unknown zone {code!r}"


def test_no_neighborhood_has_empty_zone_codes():
    for n in neighborhoods.list_neighborhoods():
        assert n.zone_codes, f"{n.slug_en} has no zone_codes"


def test_slugs_are_ascii_lowercase_kebab_case():
    for n in neighborhoods.list_neighborhoods():
        for slug in (n.slug_it, n.slug_en):
            assert slug.isascii(), f"{slug!r} is not ASCII"
            assert _SLUG_RE.match(slug), f"{slug!r} is not kebab-case"


def test_slugs_are_unique_per_language():
    all_n = neighborhoods.list_neighborhoods()
    slugs_it = [n.slug_it for n in all_n]
    slugs_en = [n.slug_en for n in all_n]
    assert len(slugs_it) == len(set(slugs_it)), "duplicate slug_it"
    assert len(slugs_en) == len(set(slugs_en)), "duplicate slug_en"


def test_neighborhood_for_slug_round_trips_both_langs():
    for n in neighborhoods.list_neighborhoods():
        assert neighborhoods.neighborhood_for_slug(n.slug_it, "it") == n
        assert neighborhoods.neighborhood_for_slug(n.slug_en, "en") == n


def test_neighborhood_for_slug_unknown_returns_none():
    assert neighborhoods.neighborhood_for_slug("not-a-real-place", "it") is None
    assert neighborhoods.neighborhood_for_slug("not-a-real-place", "en") is None


def test_neighborhood_for_slug_unsupported_lang_returns_none():
    assert neighborhoods.neighborhood_for_slug("brera", "fr") is None


def test_zones_for_neighborhood_matches_neighborhood_zone_codes():
    for n in neighborhoods.list_neighborhoods():
        assert neighborhoods.zones_for_neighborhood(n.slug_it, "it") == n.zone_codes
        assert neighborhoods.zones_for_neighborhood(n.slug_en, "en") == n.zone_codes


def test_zones_for_neighborhood_unknown_returns_empty_tuple():
    assert neighborhoods.zones_for_neighborhood("not-a-real-place", "it") == ()


def test_isola_and_nolo_are_present_and_resolved():
    # These were "???" in the initial draft and needed geocode verification.
    isola = neighborhoods.neighborhood_for_slug("isola", "it")
    nolo = neighborhoods.neighborhood_for_slug("nolo", "it")
    assert isola is not None and isola.zone_codes
    assert nolo is not None and nolo.zone_codes
