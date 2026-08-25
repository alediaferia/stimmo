"""Colloquial neighborhood name -> OMI zone-code alias layer.

`/{lang}/zones/{code}` pages are addressed by OMI cadastral code (B15, D12, ...), but
nobody searches "B15" — they search "Brera". This module is a curated, hand-checked
lookup table from the neighborhood names Milanese people actually use to the OMI zone
code(s) that cover them, and is the data foundation for the neighborhood-slug pages
registered in web/app.py ("neighborhood_detail": /{lang}/milano-or-milan/{slug}...).

Pure name<->code mapping — no pricing/valuation logic belongs here (see
`valuation/adjustments.py` for the one tuning surface). The table below is static: it
was built once, offline, by geocoding each candidate name with `data.geocode.geocode`
and resolving the result through `data.zones.zone_for_point`, then hand-reconciling the
result against local knowledge of Milan. This module makes no network calls.

Verification note: geocoding a bare neighborhood name (e.g. "Brera") sometimes resolves
to the centroid of the wider official NIL (municipality-defined "Nucleo Identità
Locale"), which can land in a different, larger OMI zone than the colloquially
recognized district. Landmark/street-level queries inside the district (e.g. "Via Fiori
Chiari, Milano" for Brera, "Piazza Gae Aulenti, Milano" for Porta Nuova) gave the
reliable signal; bare names were used only as a first pass. "Isola" and "NoLo" had no
draft zone and were resolved this way from scratch.

OMI zone polygons are coarser than colloquial neighborhood boundaries, so a few zone
codes are legitimately claimed by two neighborhoods (C12: Isola + Porta Venezia; C14:
Isola + Porta Nuova; C18: Navigli + Tortona/Solari) — that's expected, not a bug.

Piazzale Loreto is a four-way zone junction: C12, C15, D12 and D36 all meet inside the
piazza, so no single code "is" Loreto. NoLo proper (Via Venini, Via Padova, Parco
Trotter) is unambiguously D36.

Not every OMI zone has an alias here: parks, rail yards, wholesale-market zones and
zones with no strong colloquial identity are deliberately left unaliased rather than
forcing a name onto them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Neighborhood:
    slug_it: str
    slug_en: str
    name: str  # display name, e.g. "Brera" — a proper noun, not translated
    zone_codes: tuple[str, ...]
    # 150-250 words of genuine local context, filled in a separate editorial pass —
    # not written as part of adding a neighborhood to this table. Empty by default,
    # which is deliberate: web/app.py's "neighborhood_detail" SEO route only lists a
    # neighborhood in the sitemap once BOTH blurb_it and blurb_en are non-empty (the
    # route itself still resolves and renders without one, so it's reachable and
    # internally linked — we just don't ask Google to index a thin page yet).
    blurb_it: str = ""
    blurb_en: str = ""


# Ordered roughly centro -> periphery, clockwise. Kept as a tuple literal (not built
# from OMI data) so it stays a static, hand-checked table per the "bundled, not
# fetched at runtime" invariant.
_NEIGHBORHOODS: tuple[Neighborhood, ...] = (
    Neighborhood("brera", "brera", "Brera", ("B15",)),
    # Only pair with slug_it != slug_en: "centro storico" is a generic descriptive
    # phrase ("historic center"), not a proper noun, so it gets translated. Keep it
    # that way — don't "normalize" this to match the rest of the table.
    Neighborhood(
        "duomo-centro-storico",
        "duomo-historic-center",
        "Duomo / Centro Storico",
        ("B12", "B13", "B16"),
    ),
    Neighborhood("porta-nuova", "porta-nuova", "Porta Nuova", ("C14",)),
    Neighborhood("citylife", "citylife", "CityLife", ("C13",)),
    Neighborhood("navigli", "navigli", "Navigli", ("B21", "C18")),
    # Isola: C12 covers the bulk (Borsieri, Confalonieri, Minniti, Pepe, Garigliano,
    # Thaon di Revel); C14 covers the south-east edge facing the Porta Nuova towers
    # (de Castillia, Sassetti). C15 was in an earlier draft but only matches the far
    # northern tip at Viale Stelvio, which is past Isola proper — dropped after a
    # 10-point boundary check.
    Neighborhood("isola", "isola", "Isola", ("C12", "C14")),
    Neighborhood("nolo", "nolo", "NoLo", ("D36",)),
    Neighborhood("porta-romana", "porta-romana", "Porta Romana", ("B19", "B20")),
    Neighborhood("porta-venezia", "porta-venezia", "Porta Venezia", ("B18", "C12")),
    Neighborhood("citta-studi", "citta-studi", "Città Studi", ("D12",)),
    Neighborhood("lambrate", "lambrate", "Lambrate", ("D13",)),
    Neighborhood("bicocca", "bicocca", "Bicocca", ("D34",)),
    Neighborhood("bovisa", "bovisa", "Bovisa", ("D31",)),
    Neighborhood(
        "sempione-arco-della-pace",
        "sempione-arco-della-pace",
        "Sempione / Arco della Pace",
        ("B17",),
    ),
    Neighborhood("wagner-pagano", "wagner-pagano", "Wagner / Pagano", ("C17",)),
    Neighborhood("tortona-solari", "tortona-solari", "Tortona / Solari", ("C18",)),
    Neighborhood(
        "rogoredo-santa-giulia",
        "rogoredo-santa-giulia",
        "Rogoredo / Santa Giulia",
        ("D38",),
    ),
    Neighborhood("gallaratese", "gallaratese", "Gallaratese", ("E6",)),
    Neighborhood("baggio", "baggio", "Baggio", ("E5",)),
    Neighborhood("affori", "affori", "Affori", ("D32",)),
    Neighborhood("niguarda", "niguarda", "Niguarda", ("D33",)),
    Neighborhood("corvetto", "corvetto", "Corvetto", ("D20",)),
    Neighborhood("quarto-oggiaro", "quarto-oggiaro", "Quarto Oggiaro", ("E8",)),
    Neighborhood("certosa", "certosa", "Certosa", ("D40",)),
    Neighborhood("crescenzago", "crescenzago", "Crescenzago", ("D35",)),
    Neighborhood("chiaravalle", "chiaravalle", "Chiaravalle", ("R2",)),
    Neighborhood("sarpi", "sarpi", "Sarpi", ("C16",)),
)

_BY_SLUG_IT: dict[str, Neighborhood] = {n.slug_it: n for n in _NEIGHBORHOODS}
_BY_SLUG_EN: dict[str, Neighborhood] = {n.slug_en: n for n in _NEIGHBORHOODS}


def _build_zone_index() -> dict[str, tuple[Neighborhood, ...]]:
    index: dict[str, list[Neighborhood]] = {}
    for n in _NEIGHBORHOODS:
        for code in n.zone_codes:
            index.setdefault(code, []).append(n)
    return {code: tuple(ns) for code, ns in index.items()}


_BY_ZONE: dict[str, tuple[Neighborhood, ...]] = _build_zone_index()


def list_neighborhoods() -> list[Neighborhood]:
    """All curated neighborhoods, in table order."""
    return list(_NEIGHBORHOODS)


def neighborhoods_for_zone(code: str) -> tuple[Neighborhood, ...]:
    """Curated neighborhoods that claim OMI zone `code`, in table order.

    Usually 0 or 1 neighborhood. Exactly the three zones documented in the module
    docstring (C12, C14, C18) return 2 — that's the deliberate zone-sharing overlap,
    not a bug. Used to link zone pages back up to the neighborhood page(s) that
    contain them.
    """
    return _BY_ZONE.get(code, ())


def shared_zones(n: Neighborhood) -> dict[str, tuple[Neighborhood, ...]]:
    """Map each of `n`'s zone codes that's also claimed by another curated
    neighborhood to the other neighborhood(s) sharing it (`n` itself excluded).

    Computed from the table above at call time, not hardcoded — see the module
    docstring for why C12/C14/C18 are legitimately shared today. Returns an empty
    dict for a neighborhood with no shared zones (e.g. Brera).
    """
    out: dict[str, tuple[Neighborhood, ...]] = {}
    for code in n.zone_codes:
        others = tuple(o for o in _BY_ZONE.get(code, ()) if o is not n)
        if others:
            out[code] = others
    return out


def neighborhood_for_slug(slug: str, lang: str) -> Neighborhood | None:
    """Look up a neighborhood by its language-specific slug.

    `lang` is a URL lang slug ('it' or 'en'), matching `stimmo.i18n.SUPPORTED_LANGS`.
    Returns None for an unknown slug or an unsupported lang.
    """
    if lang == "it":
        return _BY_SLUG_IT.get(slug)
    if lang == "en":
        return _BY_SLUG_EN.get(slug)
    return None


def zones_for_neighborhood(slug: str, lang: str) -> tuple[str, ...]:
    """OMI zone codes for a neighborhood slug, or () if the slug/lang is unknown."""
    n = neighborhood_for_slug(slug, lang)
    return n.zone_codes if n else ()
