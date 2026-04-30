"""Import OMI assets from an Agenzia Entrate publication directory into bundled assets.

Usage: uv run python scripts/import_omi_agenziaentrate.py <source-dir>

Where <source-dir> contains:
  - *_VALORI.csv  (market values, semicolon-sep, title row + header row)
  - *_ZONE.csv    (zone metadata, semicolon-sep, title row + header row)
  - F205.kml      (zone polygons, KML format)

Rewrites the same three asset paths that refresh_omi.py targets; the two scripts
are alternative entry points to the same asset shape.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ASSETS = Path(__file__).resolve().parent.parent / "src" / "stimmo" / "data" / "assets"
HISTORY_SEMESTERS = 8

VALORI_COLS = [
    "Area_territoriale",
    "Regione",
    "Prov",
    "Comune_ISTAT",
    "Comune_cat",
    "Sez",
    "Comune_amm",
    "Comune_descrizione",
    "Fascia",
    "Zona",
    "LinkZona",
    "Cod_Tip",
    "Descr_Tipologia",
    "Stato",
    "Stato_prev",
    "Compr_min",
    "Compr_max",
    "Sup_NL_compr",
]


def _find_file(src: Path, pattern: str) -> Path:
    matches = list(src.glob(pattern))
    if not matches:
        raise SystemExit(f"No file matching {pattern!r} in {src}")
    return matches[0]


def _semester_key(s: str) -> tuple[int, int]:
    m = re.match(r"(\d{4})-(\d)", s)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _detect_semester(csv_path: Path) -> str:
    with open(csv_path, encoding="utf-8") as fh:
        title = fh.readline().strip()
    m = re.search(r"Semestre\s+(\d{4})/(\d)", title)
    if not m:
        raise SystemExit(f"Cannot detect semester from title row: {title!r}")
    return f"{m.group(1)}-{m.group(2)}"


def import_valori(src: Path) -> tuple[pd.DataFrame, str]:
    csv = _find_file(src, "*_VALORI.csv")
    semester = _detect_semester(csv)
    df = pd.read_csv(csv, sep=";", skiprows=1, dtype=str)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df = df.drop(columns=[c for c in ("Loc_min", "Loc_max", "Sup_NL_loc") if c in df.columns])
    df = df.reindex(columns=VALORI_COLS)
    out = ASSETS / "milano_omi_valori.csv"
    df.to_csv(out, index=False)
    print(f"  valori: {csv.name} (semester {semester})")
    print(f"    → {out} ({out.stat().st_size:,} bytes, {len(df)} rows)")
    manifest = ASSETS / "manifest.json"
    manifest.write_text(f'{{"semester": "{semester}"}}\n')
    print(f"    → {manifest}")
    return df, semester


def update_history(new_df: pd.DataFrame, semester: str) -> None:
    hist_path = ASSETS / "milano_omi_history.csv"
    existing = pd.read_csv(hist_path, dtype=str)
    # Clean stray Unnamed columns (artefact of prior bad concat)
    existing = existing.loc[:, ~existing.columns.str.startswith("Unnamed")]
    # Drop any non-canonical columns (e.g. leftover title-row text promoted to column name)
    canonical_with_sem = VALORI_COLS + ["semester"]
    existing = existing.reindex(columns=canonical_with_sem)
    # Idempotency: drop rows already tagged for this semester
    existing = existing[existing["semester"] != semester]
    new_with_sem = new_df.copy()
    new_with_sem["semester"] = semester
    hist = pd.concat(
        [existing, new_with_sem.reindex(columns=canonical_with_sem)],
        ignore_index=True,
    )
    # Keep last HISTORY_SEMESTERS semesters
    all_sems = sorted(hist["semester"].dropna().unique(), key=_semester_key)
    keep = set(all_sems[-HISTORY_SEMESTERS:])
    hist = hist[hist["semester"].isin(keep)]
    hist.to_csv(hist_path, index=False)
    kept_label = ", ".join(sorted(keep, key=_semester_key))
    print(f"  history: semesters kept: {kept_label}")
    print(f"    → {hist_path} ({hist_path.stat().st_size:,} bytes, {len(hist)} rows total)")


def import_zones(src: Path, old_zone_codes: set[str]) -> None:
    kml = src / "F205.kml"
    if not kml.exists():
        raise SystemExit(f"F205.kml not found in {src}")
    zone_csv = _find_file(src, "*_ZONE.csv")

    # Build zone_code → description from ZONE.csv
    zones_df = pd.read_csv(zone_csv, sep=";", skiprows=1, dtype=str)
    zones_df = zones_df.loc[:, ~zones_df.columns.str.startswith("Unnamed")]
    zone_descr: dict[str, str] = {}
    if "Zona" in zones_df.columns and "Zona_Descr" in zones_df.columns:
        for _, row in zones_df.iterrows():
            code = str(row["Zona"]).strip()
            descr = str(row["Zona_Descr"]).strip().strip("'")
            zone_descr[code] = descr

    try:
        gdf = gpd.read_file(kml, engine="pyogrio")
    except Exception as exc:
        raise SystemExit(
            f"Failed to read KML with pyogrio: {exc}\n"
            "Ensure pyogrio is installed with LIBKML driver support (check: pyogrio.list_drivers())."
        ) from exc

    if gdf.empty:
        raise SystemExit("KML produced an empty GeoDataFrame — check LIBKML driver availability")

    if "Name" not in gdf.columns:
        raise SystemExit(f"Expected 'Name' column in KML GeoDataFrame, got: {list(gdf.columns)}")

    gdf["CODZONA"] = gdf["Name"].str.extract(r"MILANO - Zona OMI (\S+)")
    gdf["Zona_Descr"] = gdf["CODZONA"].map(zone_descr)

    new_codes = set(gdf["CODZONA"].dropna())
    added = new_codes - old_zone_codes
    removed = old_zone_codes - new_codes
    if added:
        print(f"  zones: NEW in this semester (not in previous): {sorted(added)}")
    if removed:
        print(f"  zones: REMOVED vs previous semester: {sorted(removed)}")

    keep = [c for c in ("Name", "CODZONA", "Zona_Descr", "geometry") if c in gdf.columns]
    out = ASSETS / "milano_omi_zones.geojson"
    gdf[keep].to_file(out, driver="GeoJSON")
    print(f"  zones: {kml.name}")
    print(f"    → {out} ({out.stat().st_size:,} bytes, {len(gdf)} zones)")


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <source-dir>", file=sys.stderr)
        return 1
    src = Path(sys.argv[1]).resolve()
    if not src.is_dir():
        print(f"Error: {src} is not a directory", file=sys.stderr)
        return 1

    print("Importing OMI assets from Agenzia Entrate publication …")

    # Read old zone codes before overwriting for drift comparison
    old_valori = ASSETS / "milano_omi_valori.csv"
    old_zone_codes: set[str] = set()
    if old_valori.exists():
        old_df = pd.read_csv(old_valori, dtype=str)
        old_zone_codes = set(old_df["Zona"].dropna())

    df, semester = import_valori(src)
    update_history(df, semester)
    import_zones(src, old_zone_codes)

    print()
    print(f"Done. Bundled assets updated to semester {semester!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
