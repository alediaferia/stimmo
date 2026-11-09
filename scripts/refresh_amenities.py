"""Refresh the bundled amenity extract (``amenities.csv.gz``) from an OpenStreetMap snapshot.

Reads a Geofabrik regional PBF, keeps exactly the objects the live Overpass query in
``stimmo.data.amenities._query`` selects (same tag filters, same node/way split, relations
excluded, the "large park" proxy of more than 20 way vertices), clips them to the Milano
comune (union of the bundled OMI zone polygons) buffered by 1 km, and writes a gzip CSV
plus the snapshot date into ``manifest.json``.

Run: `uv run --group data python scripts/refresh_amenities.py`

The PBF (~600 MB) is downloaded to a cache directory (default: the system temp dir) and is
never committed.  ``--pbf PATH`` uses an existing file instead of downloading.  The snapshot
date is the PBF's replication timestamp, stored as ``osm_snapshot`` in the manifest.

Source: https://download.geofabrik.de/europe/italy/nord-ovest-latest.osm.pbf.  Geofabrik splits
Italy into five macro-regions (nord-ovest, nord-est, centro, sud, isole); Lombardia is inside
nord-ovest, the smallest extract that contains Milano.

The asset is derived from OpenStreetMap and is therefore under the ODbL 1.0
(see ``src/stimmo/data/assets/amenities.LICENSE``); the code stays Apache-2.0.

CSV columns: ``kind, osm_type, osm_id, name, lat, lon, geom``.  A node has ``lat``/``lon``; a
way has an empty ``lat``/``lon`` and ``geom`` = ``lat lon;lat lon;...`` (every vertex, the
closing one included, as Overpass ``out geom`` returns them), because the runtime reproduces
Overpass's "any vertex within radius, or the point inside the polygon" rule and the
vertex-mean centroid used for the map pin.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import sys
import tempfile
from pathlib import Path

import requests

ASSETS = Path(__file__).resolve().parent.parent / "src" / "stimmo" / "data" / "assets"
PBF_URL = "https://download.geofabrik.de/europe/italy/nord-ovest-latest.osm.pbf"
OUT_NAME = "amenities.csv.gz"
BUFFER_M = 1000
COORD_DECIMALS = 6
UA = "stimmo-data-refresher (https://github.com/alediaferia/stimmo)"
KEYS = ("railway", "station", "leisure", "shop", "amenity")
COLUMNS = ["kind", "osm_type", "osm_id", "name", "lat", "lon", "geom"]
KIND_ORDER = ["metro", "tram", "park", "supermarket", "school", "pharmacy"]


def selected(osm_type: str, tags: dict[str, str]) -> bool:
    """Mirror of the clauses in ``stimmo.data.amenities._query`` (node/way only)."""
    if osm_type == "node":
        return (
            (tags.get("railway") == "station" and tags.get("station") == "subway")
            or tags.get("station") == "subway"
            or tags.get("railway") == "tram_stop"
            or tags.get("shop") == "supermarket"
            or tags.get("amenity") in ("school", "pharmacy")
        )
    return (
        tags.get("leisure") == "park"
        or tags.get("shop") == "supermarket"
        or tags.get("amenity") == "school"
    )


def _fmt(v: float) -> str:
    return f"{v:.{COORD_DECIMALS}f}"


def _clip_area():
    """Milano comune (union of the OMI zones) buffered by BUFFER_M, as a prepared geometry."""
    import geopandas as gpd
    from shapely.prepared import prep

    gdf = gpd.read_file(ASSETS / "milano_omi_zones.geojson").to_crs(32632)
    area = gdf.geometry.make_valid().union_all().buffer(BUFFER_M)
    area = gpd.GeoSeries([area], crs=32632).to_crs(4326).iloc[0]
    return prep(area)


def extract(pbf: Path) -> tuple[list[dict], str | None]:
    """Rows for every selected object inside the clip area, and the PBF replication date."""
    import osmium
    from shapely.geometry import LineString, Point

    from stimmo.data.amenities import _classify

    clip = _clip_area()
    rows: list[dict] = []
    fp = (
        osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.KeyFilter(*KEYS))
    )
    stamp = fp.header.get("osmosis_replication_timestamp") or None
    for o in fp:
        tags = {t.k: t.v for t in o.tags}
        if o.is_node():
            if not selected("node", tags) or not o.location.valid():
                continue
            el = {"tags": tags}
            lat, lon = o.location.lat, o.location.lon
            if not clip.contains(Point(lon, lat)):
                continue
            kind = _classify(el)
            geom = ""
            osm_type = "node"
        else:
            if not selected("way", tags):
                continue
            pts = [(n.lat, n.lon) for n in o.nodes if n.location.valid()]
            if not pts:
                continue
            el = {"tags": tags, "geometry": [{"lat": a, "lon": b} for a, b in pts]}
            kind = _classify(el)
            if kind is None:
                continue
            shape = LineString([(b, a) for a, b in pts]) if len(pts) > 1 else Point(pts[0][::-1])
            if not clip.intersects(shape):
                continue
            lat = lon = None
            geom = ";".join(f"{_fmt(a)} {_fmt(b)}" for a, b in pts)
            osm_type = "way"
        if kind is None:
            continue
        rows.append(
            {
                "kind": kind,
                "osm_type": osm_type,
                "osm_id": o.id,
                "name": tags.get("name", ""),
                "lat": "" if lat is None else _fmt(lat),
                "lon": "" if lon is None else _fmt(lon),
                "geom": geom,
            }
        )
    rows.sort(key=lambda r: (KIND_ORDER.index(r["kind"]), r["osm_type"], r["osm_id"]))
    return rows, stamp


def write_asset(rows: list[dict], out: Path) -> None:
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        import io

        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.DictWriter(text, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        text.flush()
        text.detach()


def _bundled_snapshot(assets_dir: Path | None = None) -> str | None:
    """``osm_snapshot`` from the bundled manifest (YYYY-MM-DD), or None if absent/unreadable."""
    assets_dir = assets_dir if assets_dir is not None else ASSETS
    try:
        raw = json.loads((assets_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    value = raw.get("osm_snapshot") if isinstance(raw, dict) else None
    return value if isinstance(value, str) else None


def _guard_downgrade(candidate: str, bundled: str | None, allow_downgrade: bool = False) -> None:
    """Refuse to overwrite the bundled extract with an older OSM snapshot (ISO dates compare)."""
    if bundled is None or allow_downgrade or candidate >= bundled:
        return
    raise SystemExit(
        f"Refusing to downgrade bundled amenities: PBF snapshot is {candidate}, but the "
        f"bundled manifest.json says {bundled}. Pass --allow-downgrade to overwrite anyway."
    )


def _update_manifest(snapshot: str, assets_dir: Path | None = None) -> Path:
    assets_dir = assets_dir if assets_dir is not None else ASSETS
    path = assets_dir / "manifest.json"
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["osm_snapshot"] = snapshot
    path.write_text(json.dumps(data) + "\n")
    return path


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url}")
    with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=60) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pbf", type=Path, help="use this PBF instead of downloading one")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "stimmo-osm",
        help="where the downloaded PBF is kept (never inside the repo)",
    )
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="overwrite the bundled extract even when the PBF snapshot is older",
    )
    args = parser.parse_args(argv)

    pbf = args.pbf
    if pbf is None:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        pbf = args.cache_dir / "nord-ovest-latest.osm.pbf"
        _download(PBF_URL, pbf)
    print(f"Extracting amenities from {pbf} …")
    rows, stamp = extract(pbf)
    if not stamp:
        raise SystemExit("PBF has no replication timestamp; cannot record the OSM snapshot")
    snapshot = stamp[:10]
    _guard_downgrade(snapshot, _bundled_snapshot(), allow_downgrade=args.allow_downgrade)

    ASSETS.mkdir(parents=True, exist_ok=True)
    out = ASSETS / OUT_NAME
    write_asset(rows, out)
    manifest = _update_manifest(snapshot)
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print(f"  → {out} ({out.stat().st_size:,} bytes, {len(rows)} features)")
    print("    " + ", ".join(f"{k}={by_kind.get(k, 0)}" for k in KIND_ORDER))
    print(f"  → {manifest} (osm_snapshot={snapshot})")
    print(f"Done (extracted {dt.date.today()}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
