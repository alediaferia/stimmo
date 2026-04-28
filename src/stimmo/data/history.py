"""Historical OMI €/m² series for an (OMI zone, property type, condition).

Backed by the bundled `milano_omi_history.csv` (last 8 semesters of MILANO
Compravendita quotations from dati.comune.milano.it). Each row exposes the
official OMI min/max band for the matching cell, which is itself built from
observed transactions in that semester — so the series is the closest
free-data proxy for "recent closing prices" in the zone.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import NamedTuple

import pandas as pd

from stimmo.models import OmiCondition, PropertyType

ASSET = files("stimmo.data.assets") / "milano_omi_history.csv"


class HistoryPoint(NamedTuple):
    semester: str  # e.g. "2024-2"
    eur_m2_min: float
    eur_m2_max: float

    @property
    def eur_m2_mid(self) -> float:
        return (self.eur_m2_min + self.eur_m2_max) / 2


@cache
def _df() -> pd.DataFrame:
    with ASSET.open("rb") as fh:
        df = pd.read_csv(fh, dtype=str)
    df["Compr_min"] = pd.to_numeric(df["Compr_min"], errors="coerce")
    df["Compr_max"] = pd.to_numeric(df["Compr_max"], errors="coerce")
    return df


def _semester_sort_key(s: str) -> tuple[int, int]:
    y, n = s.split("-")
    return int(y), int(n)


def series(
    zone_code: str,
    ptype: PropertyType,
    condition: OmiCondition,
) -> list[HistoryPoint]:
    """Oldest → newest. Falls back to any condition if exact bucket missing
    in some semesters (so the series stays continuous)."""
    df = _df()
    base = df[
        (df["Zona"] == zone_code) & (df["Descr_Tipologia"] == ptype.value) & df["Compr_min"].notna()
    ]
    if base.empty:
        return []

    out: list[HistoryPoint] = []
    for sem, group in base.groupby("semester"):
        match = group[group["Stato"] == condition.value]
        row = match.iloc[0] if not match.empty else group.iloc[0]
        out.append(
            HistoryPoint(
                semester=str(sem),
                eur_m2_min=float(row["Compr_min"]),
                eur_m2_max=float(row["Compr_max"]),
            )
        )
    out.sort(key=lambda p: _semester_sort_key(p.semester))
    return out
