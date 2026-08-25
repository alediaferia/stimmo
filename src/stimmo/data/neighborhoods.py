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

Editorial blurbs (`Neighborhood.blurb_it`/`blurb_en`) are the one thing in this module
that ISN'T derivable from public OMI/geocoding data, and stimmo is Apache-2.0 — committing
that prose to the public repo would grant an explicit licence to reproduce and
redistribute it (a licensee already reuses stimmo's OMI `Zona_Descr` strings verbatim, so
this isn't hypothetical). Blurb text therefore lives outside the repo, in a local JSON
file loaded at call time by `_neighborhoods_with_content()` — see that function and
`_load_blurb_content()` below. This is a deliberate, narrow exception to "data is bundled
into the repo at commit time": bundling now happens at *deploy* (the operator drops
`neighborhoods.json` into `STIMMO_CONTENT_DIR`) rather than at commit, for editorial prose
specifically. The name<->code table itself, OMI quotations and zone polygons are still
bundled and committed as usual, and no network call is involved — it's a plain local file
read, same as every other bundled asset.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class Neighborhood:
    slug_it: str
    slug_en: str
    name: str  # display name, e.g. "Brera" — a proper noun, not translated
    zone_codes: tuple[str, ...]
    # 150-250 words of genuine local context, filled in a separate editorial pass —
    # NOT a literal in `_NEIGHBORHOODS` below (see module docstring: this is licensed
    # content, kept out of the public repo). `list_neighborhoods()` /
    # `neighborhood_for_slug()` populate these two fields at call time from the
    # external content file; every `Neighborhood` constructed directly in
    # `_NEIGHBORHOODS` gets the default "". Empty is also the correct state for a
    # neighborhood whose blurb genuinely hasn't been written yet, and that's
    # deliberate either way: web/app.py's "neighborhood_detail" SEO route only lists
    # a neighborhood in the sitemap once BOTH blurb_it and blurb_en are non-empty
    # (the route itself still resolves and renders without one, so it's reachable
    # and internally linked — we just don't ask Google to index a thin page yet).
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


def _build_zone_index() -> dict[str, tuple[Neighborhood, ...]]:
    index: dict[str, list[Neighborhood]] = {}
    for n in _NEIGHBORHOODS:
        for code in n.zone_codes:
            index.setdefault(code, []).append(n)
    return {code: tuple(ns) for code, ns in index.items()}


# Zone-code index: built once from the blurb-less structural table above. Deliberately
# NOT derived from the content-enriched table below — zone lookups (used for the zone
# pages' "up" links) have nothing to do with editorial copy, so they stay unaffected by
# whether STIMMO_CONTENT_DIR is set, absent, or (in the malformed case) broken.
_BY_ZONE: dict[str, tuple[Neighborhood, ...]] = _build_zone_index()


# ---------------------------------------------------------------------------
# Editorial content loading
#
# See the module docstring for *why* blurbs live outside the repo. Mechanically:
# `neighborhoods.json` is read from `STIMMO_CONTENT_DIR` (default: repo-local,
# git-ignored `var/content/`, matching the `STIMMO_SHARE_DB` precedent in
# web/app.py) and merged onto `_NEIGHBORHOODS` by `slug_en`. Expected shape:
#
#   {"<slug_en>": {"blurb_it": "...", "blurb_en": "..."}}
#
# A slug missing from the file — or the file missing entirely — yields empty
# blurbs for that neighborhood, silently: that's the normal state for a public-repo
# clone and for any neighborhood whose copy hasn't been written yet. A file that
# exists but is malformed (bad JSON, or valid JSON that isn't an object) is a real
# operator error rather than "no content yet", so it's raised instead of swallowed.
# ---------------------------------------------------------------------------

_CONTENT_FILENAME = "neighborhoods.json"
_DEFAULT_CONTENT_DIR = Path(__file__).parent.parent.parent.parent / "var" / "content"


def _content_path() -> Path:
    content_dir = os.environ.get("STIMMO_CONTENT_DIR", str(_DEFAULT_CONTENT_DIR))
    return Path(content_dir) / _CONTENT_FILENAME


def _load_blurb_content() -> dict[str, dict[str, str]]:
    """Read+parse the external content file. See module-level comment above."""
    path = _content_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stimmo: malformed neighborhood content file at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"stimmo: neighborhood content file at {path} must be a JSON object")
    return data


def _with_content(n: Neighborhood, content: dict[str, dict[str, str]]) -> Neighborhood:
    entry = content.get(n.slug_en)
    if not isinstance(entry, dict):
        return n
    return replace(n, blurb_it=entry.get("blurb_it", ""), blurb_en=entry.get("blurb_en", ""))


@cache
def _neighborhoods_with_content() -> tuple[Neighborhood, ...]:
    """`_NEIGHBORHOODS`, each entry's blurb_it/blurb_en filled in from the external
    content file when present.

    Cached per process (not re-read on every call) both because that matches how
    every other bundled dataset in `stimmo.data` is loaded once and reused (see e.g.
    `data.omi._df`), and so `list_neighborhoods()` / `neighborhood_for_slug()` hand
    back the *same* object for a given neighborhood within one load —
    web/app.py's `_nearby_neighborhoods` compares neighborhoods with `is`, and that
    only holds if both functions are reading off one shared, stable tuple.

    Call `_neighborhoods_with_content.cache_clear()` to force a reload (e.g. after
    changing `STIMMO_CONTENT_DIR` or the file's contents) — same pattern
    `web.app._sitemap_xml` uses for its own env/registry-dependent cache.
    """
    content = _load_blurb_content()
    return tuple(_with_content(n, content) for n in _NEIGHBORHOODS)


def list_neighborhoods() -> list[Neighborhood]:
    """All curated neighborhoods, in table order, with blurbs loaded from content."""
    return list(_neighborhoods_with_content())


def neighborhoods_for_zone(code: str) -> tuple[Neighborhood, ...]:
    """Curated neighborhoods that claim OMI zone `code`, in table order.

    Usually 0 or 1 neighborhood. Exactly the three zones documented in the module
    docstring (C12, C14, C18) return 2 — that's the deliberate zone-sharing overlap,
    not a bug. Used to link zone pages back up to the neighborhood page(s) that
    contain them. Blurb-agnostic (see `_BY_ZONE` above) — never touches the content
    file.
    """
    return _BY_ZONE.get(code, ())


def shared_zones(n: Neighborhood) -> dict[str, tuple[Neighborhood, ...]]:
    """Map each of `n`'s zone codes that's also claimed by another curated
    neighborhood to the other neighborhood(s) sharing it (`n` itself excluded).

    Computed from the table above at call time, not hardcoded — see the module
    docstring for why C12/C14/C18 are legitimately shared today. Returns an empty
    dict for a neighborhood with no shared zones (e.g. Brera). Compares by slug_en
    rather than `is`, since `n` may come from the content-enriched table while the
    zone index (`neighborhoods_for_zone`, blurb-agnostic) does not.
    """
    out: dict[str, tuple[Neighborhood, ...]] = {}
    for code in n.zone_codes:
        others = tuple(o for o in neighborhoods_for_zone(code) if o.slug_en != n.slug_en)
        if others:
            out[code] = others
    return out


def neighborhood_for_slug(slug: str, lang: str) -> Neighborhood | None:
    """Look up a neighborhood by its language-specific slug.

    `lang` is a URL lang slug ('it' or 'en'), matching `stimmo.i18n.SUPPORTED_LANGS`.
    Returns None for an unknown slug or an unsupported lang.
    """
    table = _neighborhoods_with_content()
    if lang == "it":
        return next((n for n in table if n.slug_it == slug), None)
    if lang == "en":
        return next((n for n in table if n.slug_en == slug), None)
    return None


def zones_for_neighborhood(slug: str, lang: str) -> tuple[str, ...]:
    """OMI zone codes for a neighborhood slug, or () if the slug/lang is unknown."""
    n = neighborhood_for_slug(slug, lang)
    return n.zone_codes if n else ()
