"""OMI (Osservatorio Mercato Immobiliare) €/m² quotations for Milano.

Reads the bundled CSV (filtered to Comune MILANO, semester 2018-2 from the
ondata mirror — see README) and exposes a typed lookup.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files

import pandas as pd

from stimmo.models import OmiCondition, OmiQuote, PropertyType

ASSET = files("stimmo.data.assets") / "milano_omi_valori.csv"
_MANIFEST = files("stimmo.data.assets") / "manifest.json"

_CONDITION_ORDER = [OmiCondition.SCADENTE, OmiCondition.NORMALE, OmiCondition.OTTIMO]
_CONDITION_RANK = {c: i for i, c in enumerate(_CONDITION_ORDER)}


@cache
def semester() -> str:
    with _MANIFEST.open("rb") as fh:
        return json.load(fh)["semester"]


@cache
def _df() -> pd.DataFrame:
    with ASSET.open("rb") as fh:
        df = pd.read_csv(fh, dtype=str)
    df["Compr_min"] = pd.to_numeric(df["Compr_min"], errors="coerce")
    df["Compr_max"] = pd.to_numeric(df["Compr_max"], errors="coerce")
    return df


def lookup(zone_code: str, ptype: PropertyType, condition: OmiCondition) -> OmiQuote:
    df = _df()
    rows = df[
        (df["Zona"] == zone_code)
        & (df["Descr_Tipologia"] == ptype.value)
        & (df["Stato"] == condition.value)
        & df["Compr_min"].notna()
    ]
    if rows.empty:
        # Fallback: same zone+type, any condition; or same type in the same Fascia.
        rows = df[
            (df["Zona"] == zone_code)
            & (df["Descr_Tipologia"] == ptype.value)
            & df["Compr_min"].notna()
        ]
    if rows.empty:
        raise LookupError(
            f"No OMI quote for zone={zone_code} type={ptype.value} condition={condition.value}"
        )
    requested_rank = _CONDITION_RANK[condition.value]
    dists = rows["Stato"].map(_CONDITION_RANK).sub(requested_rank).abs()
    r = rows.loc[dists.idxmin()]
    return OmiQuote(
        zone_code=zone_code,
        property_type=ptype,
        condition=OmiCondition(r["Stato"]),
        eur_m2_min=float(r["Compr_min"]),
        eur_m2_max=float(r["Compr_max"]),
        semester=semester(),
    )


def available_zones() -> list[str]:
    return sorted(_df()["Zona"].dropna().unique().tolist())


def available_conditions(zone_code: str, ptype: PropertyType) -> list[OmiCondition]:
    """Return OMI conditions that have a quote for the given zone+type, ordered
    from SCADENTE to OTTIMO."""
    df = _df()
    rows = df[
        (df["Zona"] == zone_code) & (df["Descr_Tipologia"] == ptype.value) & df["Compr_min"].notna()
    ]
    states = {r for r in rows["Stato"].unique() if r in {c.value for c in OmiCondition}}
    return [c for c in _CONDITION_ORDER if c.value in states]


@cache
def zone_fascia_index() -> dict[str, str]:
    """zone_code -> OMI Fascia letter (B/C/D/E), sourced from the bundled CSV.

    Used to group zones on the /{lang}/zones index page. A handful of zones
    (e.g. the "R" rural/park zones) carry no Compr_min/max rows at all and are
    absent here — callers should fall back to the zone code's own first
    character for those.
    """
    df = _df()
    g = df.dropna(subset=["Fascia"]).groupby("Zona")["Fascia"].first()
    return {str(z): str(f) for z, f in g.items()}


def zone_quotes(zone_code: str) -> list[OmiQuote]:
    """All available (property type × condition) OMI quotes for a zone.

    Display-only aggregation over the bundled CSV for the zone detail page —
    no pricing/adjustment logic, just every quoted cell for the zone sorted
    for a stable table order. Returns an empty list for zones with no bundled
    quotations (e.g. the "R" rural/park zones).
    """
    df = _df()
    rows = df[(df["Zona"] == zone_code) & df["Compr_min"].notna()]
    out: list[OmiQuote] = []
    for _, r in rows.iterrows():
        try:
            ptype = PropertyType(r["Descr_Tipologia"])
            cond = OmiCondition(r["Stato"])
        except ValueError:
            continue
        out.append(
            OmiQuote(
                zone_code=zone_code,
                property_type=ptype,
                condition=cond,
                eur_m2_min=float(r["Compr_min"]),
                eur_m2_max=float(r["Compr_max"]),
                semester=semester(),
            )
        )
    ptype_order = {p: i for i, p in enumerate(PropertyType)}
    out.sort(key=lambda q: (ptype_order[q.property_type], _CONDITION_RANK[q.condition]))
    return out


@cache
def zone_price_index(
    ptype: PropertyType = PropertyType.CIVILI,
    condition: OmiCondition = OmiCondition.NORMALE,
) -> dict[str, tuple[float, float]]:
    """Per-zone (eur_m2_min, eur_m2_max) for a given type/condition.

    Falls back to any condition for that type when the requested one is missing,
    so every zone with residential data gets an entry.
    """
    df = _df()
    typed = df[(df["Descr_Tipologia"] == ptype.value) & df["Compr_min"].notna()]
    primary = typed[typed["Stato"] == condition.value]
    out: dict[str, tuple[float, float]] = {
        str(r["Zona"]): (float(r["Compr_min"]), float(r["Compr_max"]))
        for _, r in primary.iterrows()
    }
    for _, r in typed.iterrows():
        z = str(r["Zona"])
        if z not in out:
            out[z] = (float(r["Compr_min"]), float(r["Compr_max"]))
    return out
