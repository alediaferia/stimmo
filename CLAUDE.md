# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
uv sync                          # install deps
uv run stimmo-web                 # FastAPI app on 127.0.0.1:8000 (STIMMO_HOST/STIMMO_PORT override)
uv run pytest                    # all tests
uv run pytest tests/test_engine.py::test_name   # single test
uv run python scripts/refresh_omi.py            # refresh bundled OMI assets

# i18n catalog management (run from repo root)
uv run pybabel extract -F babel.cfg -o src/stimmo/locale/messages.pot .
find src/stimmo/locale -name "*.pot" -o -name "*.po" | xargs sed -i.bak '/^"POT-Creation-Date/d' && find src/stimmo/locale -name "*.bak" -delete
uv run pybabel update -i src/stimmo/locale/messages.pot -d src/stimmo/locale
find src/stimmo/locale -name "*.po" | xargs sed -i.bak '/^"POT-Creation-Date/d' && find src/stimmo/locale -name "*.bak" -delete
uv run pybabel compile -d src/stimmo/locale

# AI-assisted translation (requires OPENROUTER_API_KEY)
uv run python scripts/translate_po.py --locale it_IT
uv run python scripts/translate_po.py --locale it_IT --force   # re-translate all
uv run python scripts/translate_po.py --locale it_IT --dry-run # preview only
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

### i18n

All routes are prefixed `/{lang}/` (`it` or `en`). Locale negotiation order: `stimmo_lang` cookie → `Accept-Language` header → `it_IT` default.

- **`stimmo/i18n.py`** — ContextVar-backed `gettext`/`ngettext`, `use_locale()` context manager, `fmt_eur`/`fmt_pct`/`fmt_semester` formatters, `negotiate_locale`.
- **`stimmo/locale/`** — Babel catalog. `it_IT` has full Italian translations; `en_US` uses msgid (English) as-is. Run `pybabel compile` after editing `.po` files.
- **`babel.cfg`** — extraction config for Python source and Jinja2 templates (`jinja2.ext.i18n`).
- **`web/labels.py`** — renders `AdjustmentBreakdown` (structured `code`/`params`) to translated strings. `AdjustmentBreakdown.name` no longer exists; use `.code` to identify entries.
- **Bookmarklet JS** — `__STIMMO_LANG__` and `__STIMMO_ALERT__` placeholders are substituted server-side in the bookmarklet route.

Adding or changing UI strings: edit the template, run `pybabel extract` + `pybabel update`, add Italian msgstr in `it_IT/LC_MESSAGES/messages.po`, run `pybabel compile`.
