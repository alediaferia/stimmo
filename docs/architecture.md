# Architecture decisions

This document records the non-obvious design choices in stimmo so that future changes preserve the right invariants.

## Valuation engine

### OMI band is the spine — there are no comparables

Italian per-transaction sale data (rogiti) is not public. stimmo does not use listing prices from portals (immobiliare.it, idealista) as pricing inputs. The estimate is built entirely from:

1. The official OMI `Compr_min`–`Compr_max` band for the zone × property type × condition bucket.
2. A deterministic multiplier from `valuation/adjustments.py`.
3. A fixed ask-premium shift (+6 %) to move from rogito band to typical-ask band.

Do not add a "comparable listings" stage. The absence is by design: portal prices are stale, biased toward overpriced outliers, and would introduce a feedback loop (stimmo would converge toward whatever the market is asking, not toward transaction reality).

### Single tuning surface

**`valuation/adjustments.py` is the only place where coefficients live.** Floor level, lift presence, fine condition, energy class, outdoor space type, construction era, orientation, amenity score — all multipliers are explicit Python constants in that one file.

Do not move multipliers into `engine.py`, `models.py`, templates, or anywhere else. The invariant is that a developer can read and change every tuning assumption in one file, and the test suite (which covers the engine end-to-end with known inputs) will immediately surface any regression.

### Ask-shifted band

Verdicts compare the asking price to an **ask-shifted band**, not the raw OMI rogito band. The shift is `ASK_PREMIUM_PCT` (currently 6 %) defined in `valuation/verdict.py`. The tolerance for "fair" is ±5 % around the shifted mid.

Rationale: OMI records transacted prices (after negotiation). Milano asking prices historically run 5–10 % above eventual sale prices. Without the shift, a listing at the OMI mid would show as "under-priced" — which is misleading. The constant is explicit and documented; changing it changes the classification threshold for all listings.

### No machine learning

The engine is a pure function: `estimate(property, omi_quote, amenity_score) → Estimate`. Same inputs always produce the same output. There is no model to train, no weights to load, no version drift. This is a deliberate constraint — the tool's value is transparency ("here is every number and where it came from"), not predictive accuracy.

## Web layer

### Server-rendered Jinja2, no JS framework

All pages are rendered on the server. JavaScript is used only for:
- Leaflet map initialisation and zone-highlight logic (form page).
- Live geocoder debounce (form page, `fetch /api/geocode`).
- Segmented control state (form page, one 5-line function).
- Bookmarklet copy-to-clipboard and paste-fallback toggle (bookmarklet page).

There is no React, Vue, or any framework. The design prototype (`/tmp/stimmo-design/`) was React/Babel-CDN; that JSX was translated into Jinja partials when implementing. Do not introduce a JS build step or bundler — the lack of one is a feature, not an oversight.

### Band gauge geometry computed in Python

The band gauge on the result page positions elements using `left: N%` and `width: N%` inline styles. These percentages are computed in `app.py` inside the `/estimate` handler and passed as the `gauge` context dict, not derived in the template.

The viewport spans `min(ask_low, range_low) × 0.92 … max(ask_high, asking, range_high) × 1.06`. Computing this in Jinja2 would require `min`/`max` across dynamic values that Jinja's filter set doesn't cleanly support without custom filters. Keeping it in Python also means the geometry is unit-testable if needed.

The `gauge` dict shape:

```python
{
    "omi_band": {"left": float, "width": float},   # % positions
    "ask_band": {"left": float, "width": float},
    "ticks": [{"label": str, "value": float, "x": float}, ...],  # 5 ticks
    "asking_x": float,                              # marker position
}
```

If you add a new visual element to the gauge (e.g. a mid-point marker), compute its `x` in `_x()` inside the handler and add it to this dict. Do not add Jinja math for it.

### Jinja filters as formatting primitives

Two filters are registered in `app.py`:

- `eur` — formats a float as `€ N.NNN` (Italian dot-grouped thousands, no decimals).
- `pct` — formats a float as `+N.N%` or `-N.N%` (signed, one decimal).

All monetary and percentage display in templates must go through these filters. Do not use `"{:,.0f}".format(n).replace(",", ".")` for new monetary values — extend `_eur` if a variant is needed (e.g. per-m² without the € prefix). The Jinja format strings are only acceptable for values that don't fit either filter (e.g. the `%.3f` multiplier).

### Template context discipline

Each route handler owns its full template context. The `_form_context` helper centralises the shared form context (property type lists, enum values, defaults, OMI semester). The `/estimate` handler builds the result context inline.

Do not pass raw model objects deep into templates and call methods on them. Pass pre-computed scalars where possible (`gauge`, `semester_months_old`, `bucket_by_q`). This keeps templates readable and makes context shape explicit.

## Data layer

### Bundled, not fetched at runtime

`data/assets/` contains all OMI data (quotations, zone polygons, 8-semester history) and NTN transaction data. These files are committed to the repository and loaded at startup (the zones GeoJSON is cached via `@lru_cache`).

The only live network calls are:
- Nominatim geocoding (once per estimate, scoped to Milano).
- Overpass amenity count (once per estimate, 500 m radius).

If either fails, the estimate degrades gracefully: geocoding failure returns an error page; Overpass failure returns a zero `AmenityScore` and sets `amenity_warning` in the template context (displayed as a `.note` callout).

Do not add any other runtime data fetch (e.g. portal price feeds, live OMI API). The bundled-data model makes the tool reproducible and offline-capable.

### Milano comune only

`data/zones.py` point-in-polygon will return `None` for any coordinate outside the Milano comune polygon. This is expected behaviour, not a bug. The metropolitan belt (Sesto, Cinisello, Cologno, Rho, etc.) is explicitly out of scope — different OMI dynamics, different zone structure. The `/estimate` handler converts a `None` zone result into an error page with an explicit message.

### Refreshing bundled data

See [updating-omi-data.md](updating-omi-data.md) for the full procedure. After a refresh, `data/omi.py` auto-derives the current semester from the manifest — no `SEMESTER` constant to bump manually.

## Navigation and routing

### Top nav: Estimate · Bookmarklet · About

There is no "Report" nav link. The result page is only reachable by POSTing `/estimate`; there is no stateless URL for a report. The prototype's "Report" tab was locked behind "run an estimate first" state — that lock is structural in the server-rendered model (no state to lock).

### `/about` active state

The topbar uses `request.url.path` (available via FastAPI's Jinja2 integration) to set the `active` class on nav links. No JS is needed.

### Import flow

`/import` (GET and POST) accepts prefilled listing data from the bookmarklet or paste fallback. It calls `_form_context` with a `defaults_override` dict and renders `form.html` with pre-populated fields. The import source is shown as a `.note` callout above the form. All field values remain editable before submission — the import is never a direct-to-result shortcut.
