"""Milano OMI zone polygons — point-in-polygon lookup."""

from __future__ import annotations

from functools import cache
from importlib.resources import files

import geopandas as gpd
from shapely.geometry import Point

ASSET = files("stimmo.data.assets") / "milano_omi_zones.geojson"


@cache
def _gdf() -> gpd.GeoDataFrame:
    with ASSET.open("rb") as fh:
        gdf = gpd.read_file(fh)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def list_zones() -> list[tuple[str, str]]:
    """Return (zone_code, zone_description) for every Milano OMI zone, sorted by code."""
    gdf = _gdf()
    cols = gdf[["CODZONA", "Zona_Descr"]].drop_duplicates() if "Zona_Descr" in gdf.columns else gdf[["CODZONA"]].drop_duplicates()
    out = []
    for _, row in cols.iterrows():
        code = str(row["CODZONA"])
        descr = str(row["Zona_Descr"]).strip() if "Zona_Descr" in row.index else code
        out.append((code, descr))
    return sorted(out, key=lambda z: z[0])


def zone_for_point(lat: float, lon: float) -> tuple[str, str] | None:
    """Returns (zone_code, zone_description) or None if the point is outside Milan."""
    gdf = _gdf()
    p = Point(lon, lat)
    hits = gdf[gdf.geometry.contains(p)]
    if hits.empty:
        return None
    r = hits.iloc[0]
    descr = r.get("Zona_Descr") if "Zona_Descr" in gdf.columns else None
    label = str(descr).strip() if descr else str(r["Name"])
    return str(r["CODZONA"]), label
