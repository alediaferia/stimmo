"""History backfill (`refresh_omi.py --backfill`) and the Agenzia Entrate importer.

`scripts/` is not a package, so both scripts are loaded by path. Fixtures are tiny synthetic
files; no network and no bundled asset is touched (`ASSETS` is monkeypatched to tmp_path).
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


refresh_omi = _load("refresh_omi")
imp = _load("import_omi_agenziaentrate")

COLS = imp.VALORI_COLS


def _rows(zones: list[str], lo: int = 1000) -> list[list[str]]:
    return [
        ["NORD-OVEST", "LOMBARDIA", "MI", "3015146", "C1AA", " ", "F205", "MILANO", "B", z,
         f"MI{z}", "20", "Abitazioni civili", "NORMALE", "P", str(lo), str(lo + 500), "L"]
        for z in zones
    ]  # fmt: skip


def _history(sems: list[str]) -> pd.DataFrame:
    frames = []
    for s in sems:
        df = pd.DataFrame(_rows(["B12", "C1"]), columns=COLS)
        df["semester"] = s
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


SEMS8 = ["2021-2", "2022-1", "2022-2", "2023-1", "2023-2", "2024-1", "2024-2", "2025-2"]


def test_merge_history_inserts_sorts_and_trims_to_eight():
    existing = _history(SEMS8)
    new = pd.DataFrame(_rows(["B12", "C1", "D1"]), columns=COLS)
    out = refresh_omi.merge_history(existing, new, "2025-1")
    sems = list(dict.fromkeys(out["semester"]))
    assert sems == ["2022-1", "2022-2", "2023-1", "2023-2", "2024-1", "2024-2", "2025-1", "2025-2"]
    assert (out["semester"] == "2025-1").sum() == 3
    assert list(out.columns) == list(existing.columns)


def test_merge_history_is_idempotent():
    existing = _history(SEMS8)
    new = pd.DataFrame(_rows(["B12"]), columns=COLS)
    once = refresh_omi.merge_history(existing, new, "2025-1")
    twice = refresh_omi.merge_history(once, new, "2025-1")
    pd.testing.assert_frame_equal(once, twice)


def test_backfill_rejects_bad_label():
    with pytest.raises(SystemExit):
        refresh_omi.backfill_semester("2025-3")


def test_backfill_touches_only_history(tmp_path, monkeypatch):
    (tmp_path / "milano_omi_history.csv").write_text(_history(SEMS8).to_csv(index=False))
    (tmp_path / "manifest.json").write_text('{"semester": "2025-2"}\n')
    (tmp_path / "milano_omi_valori.csv").write_text("sentinel")
    pkg = {"name": "ds1-quotazioni-immobiliari-omi-compravendita-semestre-2025-1"}
    monkeypatch.setattr(refresh_omi, "_ckan_search", lambda q: [pkg])
    monkeypatch.setattr(
        refresh_omi, "_fetch_semester", lambda p: pd.DataFrame(_rows(["B12"]), columns=COLS)
    )
    refresh_omi.backfill_semester("2025-1", assets_dir=tmp_path)
    assert (tmp_path / "manifest.json").read_text() == '{"semester": "2025-2"}\n'
    assert (tmp_path / "milano_omi_valori.csv").read_text() == "sentinel"
    h = pd.read_csv(tmp_path / "milano_omi_history.csv", dtype=str)
    assert "2025-1" in set(h["semester"]) and "2021-2" not in set(h["semester"])


# --- Agenzia Entrate importer -------------------------------------------------------------

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
{marks}
</Document></kml>"""
MARK = (
    "<Placemark><name>MILANO - Zona OMI {code}</name><Polygon><outerBoundaryIs><LinearRing>"
    "<coordinates>9.1,45.4,0 9.2,45.4,0 9.2,45.5,0 9.1,45.4,0</coordinates>"
    "</LinearRing></outerBoundaryIs></Polygon></Placemark>"
)


def _write_source(src: Path, title: str = "Quotazioni - Semestre 2026/1", poly: str = "kml"):
    src.mkdir()
    body = ";".join(COLS) + "\n" + "\n".join(";".join(r) for r in _rows(["B12", "C1"], 2000))
    (src / "QIP_1_20261_VALORI.csv").write_text(f"{title};;\n{body}\n", encoding="utf-8")
    (src / "QIP_1_20261_ZONE.csv").write_text(
        "Zone;;\nZona;Zona_Descr\nB12;'CENTRO'\nC1;'SEMICENTRO'\n", encoding="utf-8"
    )
    kml = KML.format(marks="".join(MARK.format(code=c) for c in ("B12", "C1")))
    if poly == "kml":
        (src / "F205.kml").write_text(kml)
    elif poly == "other-kml":
        (src / "Milano_2026.kml").write_text(kml)
    else:
        with zipfile.ZipFile(src / "F205.kmz", "w") as z:
            z.writestr("doc.kml", kml)


@pytest.fixture
def assets(tmp_path, monkeypatch):
    a = tmp_path / "assets"
    a.mkdir()
    (a / "milano_omi_history.csv").write_text(_history(SEMS8).to_csv(index=False))
    (a / "manifest.json").write_text(json.dumps({"semester": "2025-2", "osm_snapshot": "x"}))
    (a / "milano_omi_valori.csv").write_text(
        pd.DataFrame(_rows(["B12", "C1"]), columns=COLS).to_csv(index=False)
    )
    monkeypatch.setattr(imp, "ASSETS", a)
    return a


@pytest.mark.parametrize("poly", ["kml", "other-kml", "kmz"])
def test_import_2026_1_updates_everything(assets, tmp_path, poly):
    src = tmp_path / "src"
    _write_source(src, poly=poly)
    assert imp.main([str(src)]) == 0

    assert json.loads((assets / "manifest.json").read_text()) == {
        "semester": "2026-1",
        "osm_snapshot": "x",
    }
    valori = pd.read_csv(assets / "milano_omi_valori.csv", dtype=str)
    assert set(valori["Compr_min"]) == {"2000"}
    hist = pd.read_csv(assets / "milano_omi_history.csv", dtype=str)
    sems = list(dict.fromkeys(hist["semester"]))
    assert sems == [*SEMS8[1:], "2026-1"]  # appended, sorted, trimmed to 8
    geo = json.loads((assets / "milano_omi_zones.geojson").read_text())
    codes = {f["properties"]["CODZONA"] for f in geo["features"]}
    assert codes == {"B12", "C1"}
    assert {f["properties"]["Zona_Descr"] for f in geo["features"]} == {"CENTRO", "SEMICENTRO"}


def test_semester_override_and_mismatch(assets, tmp_path):
    src = tmp_path / "src"
    _write_source(src, title="Quotazioni OMI primo semestre 2026")
    with pytest.raises(SystemExit):
        imp.main([str(src)])  # unreadable title and no override
    assert imp.main([str(src), "--semester", "2026-1"]) == 0


def test_override_contradicting_title_is_error(tmp_path):
    src = tmp_path / "src"
    _write_source(src)
    with pytest.raises(SystemExit):
        imp._detect_semester(next(src.glob("*_VALORI.csv")), "2025-2")


def test_missing_polygons_is_error(assets, tmp_path):
    src = tmp_path / "src"
    _write_source(src)
    (src / "F205.kml").unlink()
    with pytest.raises(SystemExit):
        imp.main([str(src)])
