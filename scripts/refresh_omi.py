"""Refresh bundled OMI assets to the latest semester published by Comune di Milano.

Discovers the latest "Quotazioni Immobiliari OMI: Compravendita" + "Zone e Perimetri OMI"
datasets via the CKAN API and rewrites the bundled CSV / GeoJSON in place.

Run: `uv run python scripts/refresh_omi.py`

Provenance of the currently bundled assets (established 2026-09-06)
-------------------------------------------------------------------
The bundled 2025-2 vintage did **not** come from this script — it came from
`scripts/import_omi_agenziaentrate.py`, fed by a manual Agenzia Entrate download.

Evidence: `refresh_valori()` rebuilds `milano_omi_history.csv` from `candidates[:8]`,
which is always a *contiguous* run of the newest CKAN semesters. The bundled history
holds 2021-2, 2022-1, 2022-2, 2023-1, 2023-2, 2024-1, 2024-2, 2025-2 — a gap at 2025-1.
Only `import_omi_agenziaentrate.update_history()` can produce that shape: it appends one
semester onto the existing file and then trims to the last 8. So a CKAN run laid down
2021-1…2024-2 (8 contiguous), the Agenzia Entrate import added 2025-2, and the trim
dropped 2021-1 — leaving exactly the eight semesters bundled today.

Consequence: CKAN lags Agenzia Entrate. As of 2026-09-06 the newest CKAN compravendita
package is 2024-2, two semesters behind the bundled 2025-2, so an unguarded run of this
script would silently *downgrade* the bundled data. Hence the check in `refresh_valori()`;
when it fires, the fix is almost always to use the Agenzia Entrate importer instead.
"""

from __future__ import annotations

import argparse
import io
import json
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


def _bundled_semester(assets_dir: Path | None = None) -> tuple[int, int] | None:
    """Semester recorded in the bundled manifest, or None if absent/unreadable.

    Read straight off disk rather than through `stimmo.data.omi.semester()`: that one is
    `@cache`d and resolves via `importlib.resources`, which would couple this script to the
    installed runtime package for no gain.
    """
    assets_dir = assets_dir if assets_dir is not None else ASSETS
    manifest = assets_dir / "manifest.json"
    try:
        raw = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return None
    value = raw.get("semester") if isinstance(raw, dict) else None
    return _semester_key(value) if isinstance(value, str) else None


def _guard_downgrade(
    candidate: tuple[int, int],
    bundled: tuple[int, int] | None,
    allow_downgrade: bool = False,
) -> None:
    """Refuse to overwrite bundled assets with an older semester.

    An equal semester passes (re-running repairs a corrupted asset) and so does a missing or
    unparseable manifest (nothing to downgrade from on a bootstrap).
    """
    if bundled is None or allow_downgrade or candidate >= bundled:
        return
    raise SystemExit(
        f"Refusing to downgrade bundled OMI data: newest CKAN semester is "
        f"{candidate[0]}-{candidate[1]}, but the bundled manifest.json says "
        f"{bundled[0]}-{bundled[1]}. CKAN lags Agenzia Entrate — the newer vintage most "
        f"likely came from scripts/import_omi_agenziaentrate.py; use that importer instead. "
        f"Pass --allow-downgrade to overwrite anyway (deliberate rollback)."
    )


def refresh_valori(allow_downgrade: bool = False) -> str:
    pkgs = _ckan_search("quotazioni immobiliari OMI compravendita")
    candidates = [
        p for p in pkgs if "compravendita-semestre" in p["name"] and _semester_key(p["name"])
    ]
    candidates.sort(key=lambda p: _semester_key(p["name"]), reverse=True)
    if not candidates:
        raise SystemExit("No compravendita packages found")

    latest = candidates[0]
    latest_sem = _semester_key(latest["name"])
    _guard_downgrade(latest_sem, _bundled_semester(), allow_downgrade=allow_downgrade)
    print(f"  valori: {latest['title']} (semester {latest_sem})")
    df = _fetch_semester(latest)
    out = ASSETS / "milano_omi_valori.csv"
    df.to_csv(out, index=False)
    print(f"    → {out} ({out.stat().st_size:,} bytes, {len(df)} rows)")
    manifest = ASSETS / "manifest.json"
    manifest.write_text(f'{{"semester": "{latest_sem[0]}-{latest_sem[1]}"}}\n')
    print(f"    → {manifest}")

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="overwrite bundled assets even when CKAN's newest semester is older "
        "than the bundled one (deliberate rollback)",
    )
    args = parser.parse_args(argv)

    ASSETS.mkdir(parents=True, exist_ok=True)
    print("Refreshing bundled OMI assets …")
    sem = refresh_valori(allow_downgrade=args.allow_downgrade)
    refresh_zones()
    refresh_ntn()
    print()
    print(f"Done. Bundled assets updated to semester {sem!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
