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

**`valuation/adjustments.py` is the only place where valuation coefficients live.** (What a purchase costs on top of the price has its own, separate surface — see [Purchase-cost calculator](#purchase-cost-calculator).) Floor level, lift presence, fine condition, energy class, outdoor space type, construction era, orientation, amenity score — all multipliers are explicit Python constants in that one file.

Do not move multipliers into `engine.py`, `models.py`, templates, or anywhere else. The invariant is that a developer can read and change every tuning assumption in one file, and the test suite (which covers the engine end-to-end with known inputs) will immediately surface any regression.

### Ask-shifted band

Verdicts compare the asking price to an **ask-shifted band**, not the raw OMI rogito band. The shift is `ASK_PREMIUM_PCT` (currently 6 %) defined in `valuation/verdict.py`. The tolerance for "fair" is ±5 % around the shifted mid.

Rationale: OMI records transacted prices (after negotiation). Milano asking prices historically run 5–10 % above eventual sale prices. Without the shift, a listing at the OMI mid would show as "under-priced" — which is misleading. The constant is explicit and documented; changing it changes the classification threshold for all listings.

### No machine learning

The engine is a pure function: `estimate(property, omi_quote, amenity_score) → Estimate`. Same inputs always produce the same output. There is no model to train, no weights to load, no version drift. This is a deliberate constraint — the tool's value is transparency ("here is every number and where it came from"), not predictive accuracy.

## Purchase-cost calculator

`stimmo/costs/` computes what buying a Milan home costs on top of the price: purchase taxes, notary, agency, mortgage costs and an optional renovation. It sits beside the valuation, not inside it.

### A second, separate tuning surface

**`costs/rates.py` is the only place where transaction-cost rates live.** It is a declared second tuning surface next to `valuation/adjustments.py`, and the two are deliberately kept apart: the valuation answers "is the price fair?", the costs answer "what else will I pay?". `engine.estimate()`, `Estimate` and the verdict do not import or read it, and `costs/` does not import `valuation/` (both checked in `tests/test_costs_regression.py`, which also pins the engine's output to golden values so a cost change can never move a valuation).

Rules the file follows, because the figures are advice-adjacent and some are not firm:

- every constant cites its law or source in a comment;
- every benchmark (a range, not a statute) carries `as_of`, `source_url` and a review interval (`review_months`; `due` = `as_of` + interval). Statutory figures have no interval: they are re-verified after each budget law (`STATUTORY_AS_OF`), and the review schedule carries one annual reminder for that (`rates.statutory_review()`, due 15 January of the year after `STATUTORY_AS_OF`) that only notifies the maintainer and never marks a line stale. `RATES_AS_OF` is derived as the latest review of any figure;
- a range that is not confirmed current, or has no public source, is `indicative=True` and is rendered as an indicative range — an unsourced benchmark cannot be built without that flag;
- a figure that is not in a cited source is left out, not guessed.

### Pure calculator, structured output

`calculator.compute(PurchaseScenario) -> CostBreakdown` takes no clock and does no I/O (the scenario's `as_of` decides which bonus years are shown and which benchmark ranges are past their review date). It returns `CostLine`s — `code` + `params` + `CostStatus` (`statutory`, `contractual`, `benchmark`, `indicative`, `needs_input`) + low/central/high — which `web/labels.py` renders per locale. The shared types are in `models.py`, so the calculator is frontend-agnostic like the engine.

A line built from a benchmark past its `due` date carries `stale=True` (and `due`), separate from its `CostStatus` because a line can be both indicative and stale; one `ranges_overdue` note counts them and gives the oldest review date, and the web layer renders the "to be checked" badge and the note through `web/labels.py`. The web layer and the MCP tool supply today's date; `scripts/check_rates.py` lists what is overdue or due soon (exit 1 when something is overdue). Staleness is presentational: it never changes a figure or a total.

Renovation IVA is a flat 10 % on works plus contingency (`rates.RENOVATION_IVA`). The rule is finer: "beni significativi" beyond the contract value net of those goods, and materials the client buys directly, are at the ordinary rate, so the line can only understate. It is disclosed, not modelled: the input (value of the beni significativi, regime of the works) does not exist and a default split would be an invented coefficient. Every renovation scenario carries a `renovation_vat_partial` note, the Not included list names it, and the technical-fees range is declared IVA-included.

Totals are closing costs, renovation, total on top of the price and cash needed. The renovation-bonus deduction is a separate `offset` group: it is shown per payment year and never subtracted from the totals, because it is spread over ten years and depends on the buyer's income.

### Missing inputs are bounded, not estimated

Listings rarely state the rendita catastale, which drives the registro tax. The calculator does not estimate it. Without it the registro line is `needs_input`: the floor is the per-deed minimum, the ceiling is the rate applied to the price (what is owed without a prezzo-valore request), and the totals budget the ceiling so a missing input never understates the cash needed. A note asks the user to enter the rendita. Likewise the notary bands are published only up to €465k (purchase) and €370k (mortgage): above that the top band is reused and flagged rather than extrapolated.

### The mortgage: an amount or a share of the price

The mortgage is an amount in € or a percentage of the price (LTV), never both (`PurchaseScenario.mortgage_eur` / `mortgage_ltv_pct`). `PurchaseScenario.mortgage_amount_eur` is the single place a percentage becomes whole euros; the imposta sostitutiva, the mortgage-deed notary band, the down payment and the notes all read it. `CostBreakdown.cash_needed` is `down_payment_eur` + `total_on_top`, reported as those two parts.

The loan-to-value rules (the 80 % mutuo-fondiario limit, art. 38 TUB / CICR 22 April 1995, raised to 100 % only with integrative guarantees; the Consap Fondo Garanzia prima casa) live in `costs/rates.py` with their citations and only feed `CostNote`s. They are general information, stated neutrally ("as a rule", "ask the bank"). **No note may say or imply that the buyer qualifies for, can obtain or can afford a loan, and none recommends a lender or product**; there is no eligibility scoring. The percentage is computed on the price while the bank applies its limits to its own appraised value, so the UI always presents it as an approximation.

### The mortgage payment: a calculation on the user's own numbers

With a mortgage **and** a rate, `compute()` also returns `CostBreakdown.mortgage_payment` (a `MortgagePayment`): the monthly payment under French amortisation (`calculator.monthly_payment`, `P · i / (1 − (1 + i)^−n)`, `P / n` at 0 %), the total interest, the total repaid, and the total cost of the mortgage = interest + the mortgage-related lines already in the closing costs (imposta sostitutiva, mortgage-deed notary, istruttoria, perizia, insurance). The term (`mortgage_years`, default `rates.MORTGAGE_DEFAULT_YEARS` = 25; the form offers 10–30) and the nominal rate (`mortgage_rate_pct`, TAN) are scenario inputs. **The rate is never guessed**: without one there is no payment and a `mortgage_rate_missing` note; the web form pre-fills the market average, visibly and editably. Every payment carries a `mortgage_variable_rate` note (computed at today's rate; a variable rate will move it). It moves no total and no line.

The legal boundary is hard: a generic calculation on the amount, term and rate the user chooses. No affordability or payment-to-income notion, no "you can afford", no lender or product names, no eligibility (`tests/test_costs_calculator.py` pins the model's fields and the payment block's copy).

Reference rates live in `costs/rates.py` as `Benchmark`s with `review_months=3`, so the freshness mechanism covers them: Banca d'Italia, "Banche e moneta: serie nazionali", Tavola 2.4 (MIR0400), new loans to households for house purchase — the average nominal rate (net of fees, the TAN's kind of figure), the same split by initial rate-setting period (up to / over one year: Banca d'Italia publishes no fixed/variable split as such, and the copy says "typically"), and the TAEG, a different measure (fees and insurance included) shown as a TAEG and never used as a TAN. `MORTGAGE_RATE_DATA_MONTH` is the month they describe and is shown with them; they are market averages, not an offer. Refresh them from the newest monthly issue (listed at `bancaditalia.it/pubblicazioni/moneta-banche/<year>-moneta/`), update the figures, the data month, the provisional flag and the URL together, and bump the `as_of` dates.

### Web surface

- Result page: a section below the verdict, prefilled from the property (price, surface, condition → default renovation tier via `rates.FINE_CONDITION_DEFAULT_TIER`). Its form is a plain GET form to the standalone calculator, so it works without JavaScript and every scenario is a bookmarkable URL.
- Standalone calculator: `/it/costi-acquisto-casa-milano` and `/en/milan/home-buying-costs`, registered once through `_register_seo_route` (canonical, hreflang and sitemap `<lastmod>` = `RATES_AS_OF`). The bare URL renders a worked example so the indexable page always carries real numbers; a submitted scenario is served `noindex` with the bare URL as canonical. "Not included" is its own section (`#not-included`, which the estimate's one-line summary of the exclusions links to) and the methodology (`#method`) is a native, collapsed `<details>`; `static/costs-form.js` opens it when the URL fragment points inside it. Both pages show the renovation €/m² as Milan prices.
- Interest button: env-gated by `STIMMO_DOSSIER_INTEREST`, live result pages only. A click is an anonymous same-origin POST returning 204; the only state is the `stimmo_dossier_interest_total{event,lang}` counter.

## Web layer

### Server-rendered Jinja2, no JS framework

All pages are rendered on the server. JavaScript is used only for:
- Leaflet map initialisation and zone-highlight logic (form page).
- Live geocoder debounce (form page, `fetch /api/geocode`).
- Segmented control state (form page, one 5-line function).
- Bookmarklet copy-to-clipboard and paste-fallback toggle (bookmarklet page).
- The env-gated interest button on the result page (one same-origin `fetch` POST and an inline message; without JavaScript the form still POSTs and the 204 leaves the page unchanged).
- The purchase-cost form's mortgage field (`static/costs-form.js`, loaded by the result page and the standalone calculator): switching the unit converts the value using the price field, and in % mode a 0–100 slider (with the 80% / 100% mutuo-fondiario limits) and a live equivalent appear. The limits, the translated labels and the validation messages are rendered by `_costs_form.html` into data attributes (limits from `costs/rates.py`), so the script holds no rate or copy.

The purchase-cost inputs (result page and standalone calculator) are plain GET forms and need no JavaScript: the script only enhances them, the slider markup is rendered `hidden`, and the server validates everything again (a percentage above 100 is an error, never read as euros).

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
