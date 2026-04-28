# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
uv sync                          # install deps
uv run stimmo-web                 # FastAPI app on 127.0.0.1:8000 (STIMMO_HOST/STIMMO_PORT override)
uv run pytest                    # all tests
uv run pytest tests/test_engine.py::test_name   # single test
uv run python scripts/refresh_omi.py            # refresh bundled OMI assets
```

Python >= 3.12. Dependency + script management is via `uv` (see `pyproject.toml`); don't invoke `python` / `pip` directly.

## Architecture

stimmo estimates whether a Milan listing's asking price is under/fair/over, built around a single-pass pipeline with **no ML** — the entire tuning surface is one coefficients file.

### Data flow

`web/` collects a `Property` → `valuation.engine.estimate(property, quote, amenity)` → `Estimate` → renderer.

The engine is intentionally thin (`valuation/engine.py`): it asks `adjustments.compute` for `(multiplier, flat_extras, breakdown)`, applies them to the OMI `€/m² min–max` band, multiplies by surface, then calls `verdict.classify` (±5% tolerance around the band).

### Key invariants

- **`valuation/adjustments.py` is the only tuning surface.** Floor/lift/condition/energy/outdoor/box/amenity coefficients all live there. Do not scatter multipliers into the engine, models, or renderers.
- **OMI band is the spine.** Italian per-transaction sale data is not public; the estimate is an OMI `Compr_min`–`Compr_max` band with a multiplier on top. Don't introduce "comparable listings" logic — the absence is by design.
- **Data is bundled, not fetched at runtime.** `data/assets/` carries OMI quotations, zone polygons (for point-in-polygon), and `milano_omi_history.csv` (8 semesters for the trend panel). The only live calls are Nominatim (`data/geocode.py`) and Overpass (`data/amenities.py`).
- **Milano comune only.** `data/zones.py` point-in-polygon will reject addresses in the metropolitan belt — this is expected, not a bug.
- **Refreshing data:** `scripts/refresh_omi.py` rewrites `data/assets/` from the CKAN API. After running it, bump `SEMESTER` in `data/omi.py` if the semester advanced.

### Models

`models.py` holds pydantic types shared across web and engine (`Property`, `OmiQuote`, `AmenityScore`, `Estimate`, `AdjustmentBreakdown`, enums for `PropertyType` / `OmiCondition` / `FineCondition` / `EnergyClass` / `Outdoor` / `ConstructionEra` / `Orientation`). Keep the engine frontend-agnostic.

### Frontend

- `web/` — FastAPI (`web/app.py`) + Jinja templates (`web/templates/`), entry point `web/server.py` (`stimmo-web` script).
