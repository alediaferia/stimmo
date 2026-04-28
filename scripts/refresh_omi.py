"""Refresh bundled OMI assets to the latest semester published by Comune di Milano.

Discovers the latest "Quotazioni Immobiliari OMI: Compravendita" + "Zone e Perimetri OMI"
datasets via the CKAN API and rewrites the bundled CSV / GeoJSON in place.

Run: `uv run python scripts/refresh_omi.py`
"""

from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

CKAN = "https://dati.comune.milano.it/api/3/action"
HDR = {
    "User-Agent": "Mozilla/5.0 (stimmo data refresher) AppleWebKit/537.36 Chrome/126",
    "Accept": "*/*",
    "Referer": "https://dati.comune.milano.it/",
}
ASSETS = Path(__file__).resolve().parent.parent / "src" / "stimmo" / "data" / "assets"


def _ckan_search(query: str) -> list[dict]:
    r = requests.get(
        f"{CKAN}/package_search", params={"q": query, "rows": 100}, headers=HDR, timeout=30
    )
    r.raise_for_status()
    return r.json()["result"]["results"]


def _semester_key(name: str) -> tuple[int, int] | None:
    m = re.search(r"(\d{4})[-/](\d)", name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _latest(packages: list[dict], needle: str) -> dict:
    candidates = [p for p in packages if needle in p["name"] and _semester_key(p["name"])]
    if not candidates:
        raise SystemExit(f"No package matching {needle!r}")
    candidates.sort(key=lambda p: _semester_key(p["name"]) or (0, 0), reverse=True)
    return candidates[0]


def _resource(pkg: dict, fmt: str) -> str:
    for r in pkg["resources"]:
        if (r.get("format") or "").upper() == fmt.upper():
            return r["url"]
    raise SystemExit(f"No {fmt} resource in package {pkg['name']!r}")


def _download(url: str) -> bytes:
    r = requests.get(url, headers=HDR, timeout=60)
    r.raise_for_status()
    return r.content


HISTORY_SEMESTERS = 8  # last 4 years


def _fetch_semester(pkg: dict) -> pd.DataFrame:
    csv_url = _resource(pkg, "CSV")
    raw = _download(csv_url)
    return pd.read_csv(io.BytesIO(raw), sep=";", dtype=str)


def refresh_valori() -> str:
    pkgs = _ckan_search("quotazioni immobiliari OMI compravendita")
    candidates = [
        p for p in pkgs if "compravendita-semestre" in p["name"] and _semester_key(p["name"])
    ]
    candidates.sort(key=lambda p: _semester_key(p["name"]), reverse=True)
    if not candidates:
        raise SystemExit("No compravendita packages found")

    latest = candidates[0]
    latest_sem = _semester_key(latest["name"])
    print(f"  valori: {latest['title']} (semester {latest_sem})")
    df = _fetch_semester(latest)
    out = ASSETS / "milano_omi_valori.csv"
    df.to_csv(out, index=False)
    print(f"    → {out} ({out.stat().st_size:,} bytes, {len(df)} rows)")

    # Historical bundle: last N semesters concatenated with a `semester` column.
    print(f"  history: last {HISTORY_SEMESTERS} semesters")
    frames = []
    for pkg in candidates[:HISTORY_SEMESTERS]:
        sem = _semester_key(pkg["name"])
        sem_label = f"{sem[0]}-{sem[1]}"
        try:
            sdf = _fetch_semester(pkg)
        except Exception as e:
            print(f"    ! {sem_label} skipped: {e}")
            continue
        sdf["semester"] = sem_label
        frames.append(sdf)
        print(f"    + {sem_label} ({len(sdf)} rows)")
    hist = pd.concat(frames, ignore_index=True)
    hout = ASSETS / "milano_omi_history.csv"
    hist.to_csv(hout, index=False)
    print(f"    → {hout} ({hout.stat().st_size:,} bytes, {len(hist)} rows total)")

    return f"{latest_sem[0]}-{latest_sem[1]}"


def refresh_zones() -> None:
    pkgs = _ckan_search("zone e perimetri OMI")
    pkg = _latest(pkgs, "zone-e-perimetri-omi")
    sem = _semester_key(pkg["name"])
    print(f"  zones: {pkg['title']} (semester {sem})")
    geo_url = _resource(pkg, "GeoJSON")
    raw = _download(geo_url)
    gdf = gpd.read_file(io.BytesIO(raw))
    if "Zona" in gdf.columns and "CODZONA" not in gdf.columns:
        gdf = gdf.rename(columns={"Zona": "CODZONA"})
    keep = [c for c in ("Name", "CODZONA", "Zona_Descr", "geometry") if c in gdf.columns]
    out = ASSETS / "milano_omi_zones.geojson"
    gdf[keep].to_file(out, driver="GeoJSON")
    print(f"    → {out} ({out.stat().st_size:,} bytes, {len(gdf)} zones)")


# Agenzia Entrate published NTN ZIPs (residential, capoluogo+province granularity).
NTN_DEFINITIVE = (
    "https://www.agenziaentrate.gov.it/portale/documents/20143/9812824/"
    "RESIDENZIALE_DEFINITIVO_2011_2024.zip/"
    "8cb254ca-e71c-da1b-2d7a-56d859dce93d?t=1772716377382"
)
NTN_PROVISIONAL = (
    "https://www.agenziaentrate.gov.it/portale/documents/20143/9812824/"
    "RESIDENZIALE_2025_PROVV.zip/"
    "e3c9cf90-02da-b17a-1539-a239f0032a83?t=1772716376860"
)


def _milano_long(df: pd.DataFrame, value_pattern: str) -> pd.DataFrame:
    """Filter to Milano capoluogo (prov=MI, Cap=cap) and melt wide quarter cols
    matching `value_pattern` (a regex with one group for the quarter label) to long form."""
    prov_col = "prov" if "prov" in df.columns else "Prov"
    mi = df[(df[prov_col] == "MI") & (df["Cap"] == "cap")]
    if mi.empty:
        return pd.DataFrame()
    quarter_cols = [c for c in mi.columns if re.match(value_pattern, c)]
    long = mi[quarter_cols].melt(var_name="raw_col", value_name="ntn")
    long["ntn"] = pd.to_numeric(long["ntn"].astype(str).str.replace(",", "."), errors="coerce")
    return long


def refresh_ntn() -> None:
    print("  ntn: Agenzia Entrate residential volumes (Milano capoluogo)")
    frames_total: list[pd.DataFrame] = []
    frames_buckets: list[pd.DataFrame] = []

    for label, url in (
        ("definitive 2011-2024", NTN_DEFINITIVE),
        ("provisional 2025", NTN_PROVISIONAL),
    ):
        try:
            raw = _download(url)
        except Exception as e:
            print(f"    ! {label} skipped: {e}")
            continue
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = set(z.namelist())

        # RES.csv: total NTN per quarter — defs are like "2024_3" or "2025_3_PROVV_NTN".
        if "RES.csv" in names:
            res = pd.read_csv(z.open("RES.csv"), sep=";", dtype=str)
            t = _milano_long(res, r"^\d{4}_\d(_PROVV_NTN)?$")
            if not t.empty:
                t["quarter"] = t["raw_col"].str.extract(r"^(\d{4}_\d)")[0]
                frames_total.append(t[["quarter", "ntn"]].dropna(subset=["ntn"]))

        # RES_CLASSI_SUP.csv: NTN by surface bucket — cols like
        # "85 -| 115_2024_3_NTN" or "85 -| 115_2025_3_PROVV_NTN".
        if "RES_CLASSI_SUP.csv" in names:
            cls = pd.read_csv(z.open("RES_CLASSI_SUP.csv"), sep=";", dtype=str)
            b = _milano_long(cls, r"^.+_\d{4}_\d(_PROVV)?_NTN$")
            if not b.empty:
                parsed = b["raw_col"].str.extract(
                    r"^(?P<bucket>.+)_(?P<quarter>\d{4}_\d)(?:_PROVV)?_NTN$"
                )
                b = pd.concat([parsed, b["ntn"]], axis=1)
                frames_buckets.append(b[["bucket", "quarter", "ntn"]].dropna(subset=["ntn"]))

        print(f"    + {label}")

    if frames_total:
        out = ASSETS / "milano_ntn_total.csv"
        df = pd.concat(frames_total, ignore_index=True).drop_duplicates(
            subset=["quarter"], keep="last"
        )
        df.sort_values("quarter").to_csv(out, index=False)
        print(f"    → {out} ({out.stat().st_size:,} bytes, {len(df)} quarters)")

    if frames_buckets:
        out = ASSETS / "milano_ntn_by_size.csv"
        df = pd.concat(frames_buckets, ignore_index=True).drop_duplicates(
            subset=["bucket", "quarter"], keep="last"
        )
        df.sort_values(["bucket", "quarter"]).to_csv(out, index=False)
        print(f"    → {out} ({out.stat().st_size:,} bytes, {len(df)} rows)")


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    print("Refreshing bundled OMI assets …")
    sem = refresh_valori()
    refresh_zones()
    refresh_ntn()
    print()
    print(f"Done. Update SEMESTER constant in src/stimmo/data/omi.py to {sem!r} if it changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
