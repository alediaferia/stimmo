# Updating bundled OMI data

OMI (Osservatorio Mercato Immobiliare) quotations are published by Agenzia delle Entrate every semester (H1 = June, H2 = December). This document describes how to pull a new semester's data into the bundled assets.

## When to update

Agenzia delle Entrate publishes new data before Comune di Milano mirrors it on CKAN (the source used by `scripts/refresh_omi.py`). Until the CKAN mirror catches up, use the manual import path described here. Once Comune di Milano publishes the semester, `uv run python scripts/refresh_omi.py` is sufficient and no manual steps are needed.

## Step 1 — Download from Forniture OMI

1. Log in to [Forniture OMI](https://wwwt.agenziaentrate.gov.it/fotoweb/archives/5012-Forniture-OMI/) with your personal Agenzia delle Entrate credentials.
2. Navigate to the latest semester publication for **Milano (F205)**.
3. Download the ZIP archive. It will contain at minimum:
   - `QIP_<id>_<semester>_VALORI.csv` — market values
   - `QIP_<id>_<semester>_ZONE.csv` — zone metadata
   - `F205.kml` — zone polygons

## Step 2 — Unpack the archive

Create a temporary directory inside `src/stimmo/data/assets/` and unpack there:

```sh
mkdir src/stimmo/data/assets/omi-<year>-h<half>
unzip <downloaded>.zip -d src/stimmo/data/assets/omi-<year>-h<half>/
```

Example for 2025 H2:

```sh
mkdir src/stimmo/data/assets/omi-2025-h2
unzip OMI_2025_2_F205.zip -d src/stimmo/data/assets/omi-2025-h2/
```

The directory is git-ignored (`src/stimmo/data/assets/omi-*/`) — it's a staging area, not a permanent fixture.

## Step 3 — Run the importer

```sh
uv run python scripts/import_omi_agenziaentrate.py src/stimmo/data/assets/omi-<year>-h<half>
```

The script rewrites three bundled asset files in place:

| Asset | What changes |
|---|---|
| `milano_omi_valori.csv` | Replaced with new semester's rows |
| `milano_omi_history.csv` | New semester appended; trimmed to last 8 semesters |
| `milano_omi_zones.geojson` | Replaced with polygons from the new KML |

It also logs any zone codes that appeared or disappeared vs. the previous semester — review these before committing.

## Step 4 — Bump the SEMESTER constant

Open [src/stimmo/data/omi.py](../src/stimmo/data/omi.py) and update line 17:

```python
SEMESTER = "2025-2"   # ← new value
```

## Step 5 — Verify

```sh
uv run pytest          # full suite must stay green
uv run stimmo-web       # spot-check a known address in zone B12 (Centro Storico)
                       # expected band for NORMALE: ~8700–10900 €/m² in 2025-2
```

## Step 6 — Clean up

Delete the staging directory — it is large and contains no information not already captured by the imported assets:

```sh
rm -rf src/stimmo/data/assets/omi-<year>-h<half>/
```

## Notes

- `scripts/refresh_omi.py` and `scripts/import_omi_agenziaentrate.py` target the same output files — run one or the other, never both for the same semester.
- Forniture OMI requires a registered account (codice fiscale login). Comune di Milano CKAN does not.
- After CKAN publishes the semester, future refreshes can revert to `uv run python scripts/refresh_omi.py` with no manual download step.
