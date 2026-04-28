# stimmo

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.12+-blue)

**stimmo** — a transparent, OMI-anchored fair-price check for Milan apartment listings. No ML, no black box: every adjustment lives in [a single coefficients file](src/stimmo/valuation/adjustments.py).

A web tool that estimates whether the asking price of a Milan property
listing is **under-priced**, **fair**, or **over-priced**, using:

- **OMI** (Osservatorio del Mercato Immobiliare, Agenzia delle Entrate) — official
  €/m² ranges per OMI zone × property type × condition.
- **OpenStreetMap** — Nominatim geocoding + Overpass amenity counts.
- A small, transparent hedonic-style adjustment table (no ML).

> ⚠️ Italian sold-price data is not public. The OMI `Compr_min`–`Compr_max`
> band is the spine of the estimate; everything else (floor, condition,
> amenities…) tunes a multiplier on top of it.

## Install

```sh
uv sync
```

## Run

```sh
uv run stimmo-web
```

Serves a FastAPI + Jinja app on `http://127.0.0.1:8000` with a form for
address, surface, property type, OMI condition bucket, fine-grained condition,
floor, total floors, lift, energy class (optional), outdoor space, box auto,
construction era, orientation, second bathroom, and asking price, and renders
the valuation report (verdict, OMI band, adjustments breakdown, recent
semesters trend). Override the bind address via `STIMMO_HOST` / `STIMMO_PORT`
env vars.

## Importing from immobiliare.it

Open `/bookmarklet` in the running app, drag the **📋 stimmo** link to your
bookmarks bar, then click it on any immobiliare.it Milano listing. The
bookmarklet reads the page's `__NEXT_DATA__` JSON (already in your browser —
no server-side request is ever made to immobiliare.it) and opens stimmo with
the form prefilled: address, surface, price, floor, lift, energy class,
condition, outdoor space, construction era, bathrooms, and property type.

A paste fallback is also available on `/bookmarklet` for users who can't use
bookmarklets: paste the full page source of a listing and submit.

Review every prefilled field before estimating — schema drift is expected over
time.

## Architecture

```text
src/stimmo/
  data/
    omi.py          # bundled MILANO OMI quotations (semester 2024-2)
    zones.py        # bundled MILANO OMI zone polygons (point-in-polygon)
    history.py      # last 8 semesters of OMI €/m² for the trend panel
    ntn.py          # quarterly NTN transaction volumes (Milano capoluogo)
    geocode.py      # Nominatim
    amenities.py    # Overpass (500 m + 500–1000 m bands)
    assets/         # baked GeoJSON + CSV
  valuation/
    adjustments.py  # ALL coefficients live here — single tuning surface
    engine.py       # orchestrates: zone → base €/m² → adjustments → range
    verdict.py      # under / fair / over with ±5% tolerance
  web/              # FastAPI app + Jinja templates (`stimmo-web` script)
```

## Refreshing the data

OMI semestral assets (sales €/m² + zone polygons) are bundled in
`src/stimmo/data/assets/`. To pull the most recent semester from
`dati.comune.milano.it`:

```sh
uv run python scripts/refresh_omi.py
```

The script discovers the latest "Compravendita" + "Zone e Perimetri" datasets
via the CKAN API and rewrites both files in place. Bump the `SEMESTER`
constant in `src/stimmo/data/omi.py` if it advanced.

## "Recent closing prices"

Italy doesn't publish per-transaction sale data. The closest free proxy is the
**OMI €/m² band per semester**, which is itself derived from observed sales.
The bundled `milano_omi_history.csv` carries the last 8 semesters of MILANO
Compravendita quotations; the report shows the matching zone+type+condition
across all of them, with per-semester deltas and an overall trend.

## Caveats

- Polygon coverage is the comune di Milano only — addresses in the metropolitan
  belt (Sesto San Giovanni, Cinisello, etc.) will fail with "outside Milano".
- No live comparable listings; the band comes from OMI min/max, not from a
  fitted model. This is by design.
- Verdicts compare asking price to an ask-shifted band (OMI rogito + 6%). See `/about` for rationale.

## Tests

```sh
uv run pytest
```

## Data sources

- OMI Compravendita per semestre (Comune di Milano, CKAN):
  [dati.comune.milano.it — quotazioni OMI compravendita](https://dati.comune.milano.it/dataset/?q=quotazioni+immobiliari+OMI+compravendita)
- OMI Zone e Perimetri (Comune di Milano, CKAN):
  [dati.comune.milano.it — zone e perimetri OMI](https://dati.comune.milano.it/dataset/?q=zone+e+perimetri+omi)
- Nominatim: [nominatim.openstreetmap.org](https://nominatim.openstreetmap.org/)
- Overpass: [overpass-api.de](https://overpass-api.de/)

## License

Code: Apache-2.0 (see [LICENSE](LICENSE)).

Bundled data: OMI quotations and zone polygons are redistributed under
IODL 2.0 (Italian Open Data License) from the Comune di Milano CKAN
portal — attribution required.

Live data: OpenStreetMap (Nominatim, Overpass) under ODbL — attribution
required for any rendered output.

## AI-assisted development

This project is developed with help from Claude Code. See [CLAUDE.md](CLAUDE.md)
for project context provided to AI assistants. Per-session agent artifacts
(`.claude/plans/`, `.claude/reports/`, `.claude/settings.local.json`) are
gitignored — only `CLAUDE.md` and shared agent config are version-controlled.

## Disclaimer

stimmo is a fairness check, not an official appraisal (perizia). It does
not replace a qualified professional valuation (perito) and should not be
used as the basis for legal, fiscal, or contractual decisions.

<!-- TODO: add screenshot of result page -->
