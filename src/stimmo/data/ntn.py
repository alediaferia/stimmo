"""Quarterly NTN (Numero Transazioni Normalizzate) for Milano capoluogo.

Source: Agenzia delle Entrate — open-access semestral release of
"Volumi di compravendita residenziale" (province + capoluogo granularity).
Bundled by `scripts/refresh_omi.py`. Most recent quarter is typically newer
than the most recent OMI €/m² semester.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import NamedTuple

import pandas as pd

ASSET_TOTAL = files("stimmo.data.assets") / "milano_ntn_total.csv"
ASSET_BY_SIZE = files("stimmo.data.assets") / "milano_ntn_by_size.csv"

SIZE_BUCKETS: list[tuple[str, float, float]] = [
    ("<= 50", 0, 50),
    ("50 -| 85", 50, 85),
    ("85 -| 115", 85, 115),
    ("115 -| 145", 115, 145),
    ("> 145", 145, 1e9),
]


class NtnPoint(NamedTuple):
    quarter: str  # e.g. "2025_1"
    ntn: float


def _label_to_human(q: str) -> str:
    y, n = q.split("_")
    return f"{y}-Q{n}"


@cache
def _total() -> pd.DataFrame:
    with ASSET_TOTAL.open("rb") as fh:
        return pd.read_csv(fh)


@cache
def _by_size() -> pd.DataFrame:
    with ASSET_BY_SIZE.open("rb") as fh:
        return pd.read_csv(fh)


def _qkey(q: str) -> tuple[int, int]:
    y, n = q.split("_")
    return int(y), int(n)


def total_quarters(last_n: int = 8) -> list[NtnPoint]:
    df = _total().sort_values("quarter")
    rows = df.tail(last_n)
    return [NtnPoint(_label_to_human(r.quarter), float(r.ntn)) for r in rows.itertuples()]


def bucket_for_surface(surface_m2: float) -> str:
    for label, lo, hi in SIZE_BUCKETS:
        if lo < surface_m2 <= hi or (lo == 0 and surface_m2 <= hi):
            return label
    return SIZE_BUCKETS[-1][0]


def by_bucket_quarters(surface_m2: float, last_n: int = 8) -> tuple[str, list[NtnPoint]]:
    bucket = bucket_for_surface(surface_m2)
    df = _by_size()
    rows = df[df["bucket"] == bucket].sort_values("quarter").tail(last_n)
    points = [NtnPoint(_label_to_human(r.quarter), float(r.ntn)) for r in rows.itertuples()]
    return bucket, points


def latest_quarter() -> str | None:
    df = _total()
    if df.empty:
        return None
    return _label_to_human(df.sort_values("quarter").iloc[-1]["quarter"])
