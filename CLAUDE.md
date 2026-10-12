# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status of the project

This project is live and running at https://stimmo.it

## Commands

```sh
uv sync                          # install deps
uv run stimmo-web                 # FastAPI app on 127.0.0.1:8000 (STIMMO_HOST/STIMMO_PORT override)
uv run pytest                    # all tests
uv run pytest tests/test_engine.py::test_name   # single test
uv run python scripts/refresh_omi.py            # refresh bundled OMI assets
uv run python scripts/check_rates.py            # cost-calculator ranges overdue / due in 30 days (exit 1 if overdue)

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

## Working in this repo

- **Repository.** Hosted at github.com/alediaferia/stimmo as a public repository.
- **Default routing.** For concrete build/fix work (features, bug fixes, refactors), use the `stimmo-maintainer` subagent (Agent tool with `subagent_type: "stimmo-maintainer"`) — the hands-on implementer that knows the architecture invariants. For open-ended direction (what to build next, dependency/health/modernization, large architectural changes), use the `stimmo-architect` subagent (`subagent_type: "stimmo-architect"`) — it proposes and prioritizes, then hands implementation back to the maintainer. The top-level session stays the orchestrator: spawn these agents rather than relying on them to spawn one another.
- **Committing.** When the user asks to commit pending changes, use the `git-commit-curator` subagent (Agent tool with `subagent_type: "git-commit-curator"`); do not commit directly with Bash. Commits follow strict Conventional Commits — see `CONTRIBUTING.md`.
- **CI/CD.** For pipeline-specific work (debugging CI failures, extending GitHub Actions), use the `github-ci-pipeline-maintainer` subagent.
- **End-to-end validation.** After significant changes (engine, adjustments, importer, web, i18n, MCP), use the `stimmo-e2e-validator` subagent to smoke-test the real import → estimate flow against a live Milan listing.

## Architecture

stimmo estimates whether a Milan listing's asking price is under/fair/over, built around a single-pass pipeline with **no ML** — the entire valuation tuning surface is one coefficients file. What a purchase costs on top of the price is a separate, equally deterministic calculation with its own rates file (see "Purchase-cost calculator" below); it never feeds the verdict.

### Data flow

`web/` collects a `Property` → `valuation.engine.estimate(property, quote, amenity)` → `Estimate` → renderer.

The engine is intentionally thin (`valuation/engine.py`): it asks `adjustments.compute` for `(multiplier, flat_extras, breakdown)`, applies them to the OMI `€/m² min–max` band, multiplies by surface, then calls `verdict.classify` (±5% tolerance around the band).

### Key invariants

- **`valuation/adjustments.py` is the only *valuation* tuning surface.** Floor/lift/condition/energy/outdoor/box/amenity coefficients all live there. Do not scatter multipliers into the engine, models, or renderers.
- **`costs/rates.py` is the only *transaction-cost* tuning surface** — a declared second surface, never merged with the first. Every statutory rate, fee band and renovation €/m² tier lives there, each with a comment citing its law or source; every benchmark carries `as_of`, `source_url` and a review interval (`review_months`), and a figure with no public source (or not confirmed current) is `indicative=True`. Do not put a rate in `costs/calculator.py`, the web layer or a template, and do not make the valuation read from it (a test pins the estimate to golden values and checks the two packages do not import each other).
- **OMI band is the spine.** Italian per-transaction sale data is not public; the estimate is an OMI `Compr_min`–`Compr_max` band with a multiplier on top. Don't introduce "comparable listings" logic — the absence is by design.
- **Data is bundled, not fetched at runtime.** `data/assets/` carries OMI quotations, zone polygons (for point-in-polygon), and `milano_omi_history.csv` (8 semesters for the trend panel). The only live calls are Nominatim (`data/geocode.py`) and Overpass (`data/amenities.py`).
- **Milano comune only.** `data/zones.py` point-in-polygon will reject addresses in the metropolitan belt — this is expected, not a bug.
- **Refreshing data:** `scripts/refresh_omi.py` rewrites `data/assets/` from the CKAN API. After running it, bump `SEMESTER` in `data/omi.py` if the semester advanced.

### Purchase-cost calculator

`stimmo/costs/` answers "what does buying this really cost?" for a Milan home. It is independent of the valuation: `engine.estimate()` and `Estimate` do not know it exists.

- `costs/rates.py` — the tuning surface above (statutory taxes, conventional fee bands, renovation tiers, bonus by payment year, and the deterministic `FineCondition` → default renovation tier).
- `costs/calculator.py` — pure `compute(PurchaseScenario) -> CostBreakdown`, no I/O and no clock. Lines carry a structured `code`/`params`, a `CostStatus` and low/central/high values; `web/labels.py` renders them (`render_cost_line`, `render_cost_note`) the way it renders `AdjustmentBreakdown`.
- Output: closing subtotal, renovation subtotal, total on top of the price, cash needed — and the renovation-bonus offset, which is reported apart and **never netted** from them.
- **Mortgage payment.** With a mortgage and a rate, `compute()` also returns the monthly payment (French amortisation, formula in `calculator.py`, constants in `rates.py`), the interest and the total cost of the mortgage (interest + the mortgage lines already counted). **No affordability or payment-to-income ratio, no "you can afford", no lender or product names** — a generic calculation on the amount, term and rate the user chooses; the rate is never guessed (the form pre-fills the market average, visibly). The reference rates are `Benchmark`s with `review_months=3` from Banca d'Italia's Tavola 2.4 (new house-purchase loans): shown with their data month as market averages, not an offer; the TAEG is shown as a TAEG, never as a TAN.
- **A missing input is never guessed.** The rendita catastale (rarely in listings) is not estimated: the registro line becomes `needs_input`, bounded by the per-deed minimum and rate × price, the totals budget the ceiling, and a note asks for the rendita. Notary bands stop at €465k (purchase) / €370k (mortgage); above that the top band is reused and flagged, not extrapolated.
- Web: `web/costs_view.py` parses a plain GET query string (no JavaScript needed) into a scenario (`static/costs-form.js` only enhances the mortgage field: unit conversion and a %-of-price slider, limits read from `rates.py`); the result page shows the property's own scenario with stated defaults, and the standalone indexable calculator (`/it/costi-acquisto-casa-milano`, `/en/milan/home-buying-costs`, registered via `_register_seo_route`) renders the worked example when no query is given and `noindex`es per-visitor variants.
- **Interest button** — `STIMMO_DOSSIER_INTEREST` (unset/`0`/`false` = off) gates a button for a not-yet-available feature on live result pages (never share views). Off means nothing renders and `POST /{lang}/dossier-interest` is a 404. On, a click is a same-origin POST returning 204 — no body, cookie or identifier; a browser-marked cross-site request is refused. Counted by `stimmo_dossier_interest_total{event,lang}` in the handlers.
- Figures are re-verified after each budget law: bump `STATUTORY_AS_OF` in `costs/rates.py`. Statutory figures (taxes, LTV rules, bonus) have no review interval, so no line is ever "to be checked" because of them. They have an **annual reminder** instead: `rates.statutory_review()` is a `ReviewItem(statutory=True)` named `STATUTORY_AS_OF`, built from the constant on each call and due on **15 January of the year after `STATUTORY_AS_OF`** (2026-10-06 is due 2027-01-15; budget laws take effect on 1 January). It is in `review_schedule()`, so `scripts/check_rates.py` and the weekly `rates-freshness` workflow list it from 30 days before (due soon) and fail it as overdue from the day after, with markdown instructions to re-verify the statutory figures against the new budget law and then bump `STATUTORY_AS_OF`. **Notification only**: the calculator never reads it, so it marks no line stale and shows no badge or note, and it does not move `RATES_AS_OF` (still the latest `as_of`).
- **Freshness — ranges cannot go stale silently.** Every `Benchmark` (and the renovation €/m² table, `RENOVATION_TIERS_REVIEW`) carries `as_of` and `review_months`; `due` = `as_of` + interval. Cadence: 6 months for istruttoria, perizia, scoppio-incendio, renovation tiers, technical fees and contingency; 12 months for the notary bands, agency default, conformity check and moving/utilities. `compute()` stays pure: a line whose benchmark is past `due` on the scenario's `as_of` carries `stale=True` (and `due`), separate from `CostStatus`; one `ranges_overdue` note counts them and gives the oldest review date. No `as_of` (no clock) flags nothing. The web layer and the MCP tool supply today's date through one seam each (`web/app.py::_today`, `mcp/tools.py::_today`), which tests freeze. Statutory lines, and lines derived from other lines (`renovation_vat`, the bonus), are never stale. The badge ("to be checked") and the note render through `web/labels.py`; the MCP output exposes `stale` and `due`.
- **`RATES_AS_OF` is derived**: the latest of `STATUTORY_AS_OF` and every benchmark's `as_of` (`rates.latest_review_date()`), so it advances whenever any figure is re-reviewed. It is also the calculator's sitemap `<lastmod>`. Re-reviewing a range means editing that constant's value, `source_url` and `as_of` in `costs/rates.py`, nothing else.
- **`scripts/check_rates.py`** — lists benchmarks (and the `STATUTORY_AS_OF` reminder) overdue or due within `--within N` days (default 30): constant name, `as_of`, `due`, source, indicative. `--format text|markdown` (markdown is a table usable as a GitHub issue body, with refresh instructions); `--today YYYY-MM-DD` replaces the clock. Exit 1 when anything is overdue, 0 otherwise (due-soon alone still prints). No network. A workflow calls exactly `uv run python scripts/check_rates.py --format markdown --within 30`: keep that interface. That workflow is `.github/workflows/rates-freshness.yml` (Mondays 08:23 UTC, plus manual dispatch): it uses the script's stdout verbatim as the body of a single open `rates-freshness` issue (created or edited, never duplicated) and closes it when nothing is due; exit 1 with a table is an expected result and stays green, while a missing heading/table or any other exit status fails the job. `rates.review_schedule()` discovers every `Benchmark`, `PriceBand` table and `ReviewItem` in `rates.py`, so a new benchmark joins the schedule by existing (and must state its own `as_of` and `review_months`); the statutory reminder is added to it explicitly.

### Models

`models.py` holds pydantic types shared across web and engine (`Property`, `OmiQuote`, `AmenityScore`, `Estimate`, `AdjustmentBreakdown`, enums for `PropertyType` / `OmiCondition` / `FineCondition` / `EnergyClass` / `Outdoor` / `ConstructionEra` / `Orientation`). Keep the engine frontend-agnostic.

### Frontend

- `web/` — FastAPI (`web/app.py`) + Jinja templates (`web/templates/`), entry point `web/server.py` (`stimmo-web` script).

### MCP server

The `stimmo/mcp/` package exposes the valuation pipeline as a remote MCP server (Streamable HTTP) at `/mcp`. See [docs/mcp-server.md](docs/mcp-server.md) for the full reference. Key invariants:

- **No `app.mount("/mcp", ...)`.** Streamable HTTP is a single endpoint with no sub-paths, so exact-match dispatch is sufficient. We avoid `Mount` because it strips the prefix and 307-redirects bare `/mcp` to `/mcp/`, which not every MCP client follows on POST. `web/app.py` defines a top-level `application` ASGI callable that exact-matches `/mcp` to the MCP sub-app and routes everything else to the FastAPI `app`. `stimmo-web` runs uvicorn against `stimmo.web.app:application`, not `:app`.
- **Session manager started in the FastAPI lifespan.** `FastMCP.streamable_http_app()` is built once at import time so the session manager is a stable singleton; `_lifespan` drives `session_manager.run()`.
- **Client IP is `CF-Connecting-IP`.** `mcp/ratelimit.py` reads this header (Cloudflare-set, unspoofable because the VPS exposes no public ports). Do not read `X-Forwarded-For` — in this topology it points at the cloudflared sidecar.
- **No new pricing logic in tools.** `mcp/tools.py` is a thin async wrapper over `data/`, `valuation/engine.py` and (for `estimate_purchase_costs`) `costs/calculator.py`. Multipliers stay in `valuation/adjustments.py`, cost rates in `costs/rates.py`.
- **Cache + rate-limit interfaces.** `InMemoryCache` is behind a `Cache` protocol and the rate limiter uses an in-process dict — both designed to swap to Redis without touching tool code if a second replica is ever added.

### i18n

All routes are prefixed `/{lang}/` (`it` or `en`). Locale negotiation order: `stimmo_lang` cookie → `Accept-Language` header → `it_IT` default.

- **`stimmo/i18n.py`** — ContextVar-backed `gettext`/`ngettext`, `use_locale()` context manager, `fmt_eur`/`fmt_pct`/`fmt_semester` formatters, `negotiate_locale`.
- **`stimmo/locale/`** — Babel catalog. `it_IT` has full Italian translations; `en_US` uses msgid (English) as-is. Run `pybabel compile` after editing `.po` files.
- **`babel.cfg`** — extraction config for Python source and Jinja2 templates (`jinja2.ext.i18n`).
- **`web/labels.py`** — renders `AdjustmentBreakdown` and the purchase-cost `CostLine`/`CostNote` (structured `code`/`params`) to translated strings. `AdjustmentBreakdown.name` no longer exists; use `.code` to identify entries.
- **Bookmarklet JS** — `__STIMMO_LANG__` and `__STIMMO_ALERT__` placeholders are substituted server-side in the bookmarklet route.

Adding or changing UI strings: edit the template, run `pybabel extract` + `pybabel update`, then run `scripts/translate_po.py --locale it_IT` to fill in Italian translations (never write `msgstr` values by hand), then run `pybabel compile`.

**Translation invariant — never hand-write `msgstr` values.** All Italian translations must be produced by `scripts/translate_po.py`. Writing Italian text directly into `.po` files bypasses the approved translation pipeline and must not happen, even for short or "obvious" strings.

### SEO surface and the neighbourhood pages

stimmo publishes an indexable page family beyond the valuation form: OMI zone pages
(`/{lang}/zones`, `/{lang}/zones/{code}`) and **neighbourhood price pages**
(`/it/milano/<slug>-prezzi-al-mq`, `/en/milan/<slug>-property-prices`). The neighbourhood pages
translate colloquial Milan neighbourhood names — which do not appear anywhere in OMI's own
`Zona_Descr` strings — into the zone codes the engine understands.

- **`data/neighborhoods.py` is the alias layer.** A curated neighbourhood ↔ zone-code table. It
  makes no network calls at import or call time. OMI polygons are coarser than colloquial
  neighbourhoods, so several zones are **shared** by two neighbourhoods (C12 Isola + Porta Venezia,
  C14 Isola + Porta Nuova, C18 Navigli + Tortona/Solari). The page discloses the sharing in prose;
  do not try to resolve it with a redirect or a canonical, both of which need a single target.
  `neighborhoods_for_zone()` / `_BY_ZONE` deliberately read the **blurb-less** structural table so
  broken content can never break a zone page.

- **`_SEO_ROUTES` / `_register_seo_route()` in `web/app.py` is the single registry** of indexable
  routes. Path suffixes are **not** language-invariant — `/it/milano/brera-prezzi-al-mq` and
  `/en/milan/brera-property-prices` are the same page — so per-language slugs, hreflang alternates,
  canonicals and the sitemap all derive from this registry. A new HTML endpoint must make an
  explicit SEO decision here; a test enforces it.

- **Editorial prose lives outside this repo.** Neighbourhood blurbs are in the private
  `alediaferia/stimmo-content` repo, not here, for licensing reasons (this repo is Apache-2.0 and
  competitors already copy OMI strings verbatim). `var/content/neighborhoods.json` is git-ignored
  with a deliberate `.gitkeep` carve-out; a fresh clone renders the pages **without** blurbs, which
  is the intended state, not a bug.
  - Loaded by `data/neighborhoods.py` from `$STIMMO_CONTENT_DIR` (default: repo-local
    `var/content/`, which inside the image resolves to `/app/var/content/` — hence no env var in
    production). The loader is `functools.cache`d per process: **restart the server** after editing
    the JSON.
  - The release workflow checks `stimmo-content` out on the runner (token `CONTENT_REPO_TOKEN`),
    validates the JSON, and bakes it into the image. Consequence: **a blurb-only change still needs
    a stimmo release** — an otherwise-empty version bump.

- **Sitemap gating is partial, not a kill switch.** A neighbourhood enters the sitemap **iff both**
  `blurb_it` and `blurb_en` are non-empty — but the route still **resolves and renders**, and the
  page stays linked from the zone pages. A gated slug is therefore crawlable and can be indexed
  anyway; this is deliberate (see the comment on the `neighborhood_detail` registration), and it is
  why a "staggered launch" measured through this gate does not produce a clean experiment.
  `chiaravalle` is gated today: zone R2 has no OMI quotations, so its price band renders empty.

- **Guides.** `/it/guide/{slug}` serves Markdown from `$STIMMO_CONTENT_DIR/guides/{slug}.md`, in the
  same private `stimmo-content` repo and baked into the image at release like the blurbs
  (`var/content/guides/` is tracked only for its `.gitkeep`; the Markdown is git-ignored).
  - `data/guides.py` parses the file (front-matter rules and stable error codes in its docstring;
    line 1 must be `---`, an unknown key is an error, a malformed file raises). `web/guide_view.py`
    renders it with `markdown-it-py`: raw HTML is escaped, only `#anchor`, `/it/…` and `https://`
    links keep their `<a>`, and a link to a guide whose file does not exist is plain text, so
    publishing order does not matter. `guide.html` adds the Article/BreadcrumbList JSON-LD and a
    correction note linking to the `correzione-contenuti.yml` issue form.
  - **No file, no page.** An absent file is a plain 404 and sitemap membership is the file's
    existence; there is no blurb-style gate. With no content directory (a public clone) there are
    no guides at all.
  - **IT only by construction.** The route is registered with no `en` suffix, so there is no EN
    alternate and the sitemap carries the IT `<loc>` alone; `/en/guide/…`, `/it/guide` and
    `/it/guide/` are 404.
  - `scripts/check_guides.py DIR` runs in the release workflow over the content repo's
    `content/guides/` before it is staged (and nothing else is staged). It reuses the runtime parser
    and also fails on an `updated` date more than a day ahead and on an internal link that is not
    `/it/`, a sitemap path or `/it/guide/{slug}`. It prints only `<file>: <CODE> (line N)`, never
    source text, because run logs are public.
  - `costs_guide_link()` (`web/app.py`) links the IT calculator and result page to the guide named
    by `costs_view.COSTS_GUIDE_SLUG` once its file exists. No neighbourhood page links to a guide;
    `tests/test_guides.py` enforces it.

SEO strategy, Search Console tooling and competitive research live in the private
`stimmo-growth` repo — deliberately not here. Keep strategy, keyword research and traffic data out
of this repository.

### Observability

`web/metrics.py` wraps the top-level ASGI dispatcher with a Prometheus middleware. Collectors:

- `stimmo_http_requests_total` — Counter, labels: `method`, `route`, `status`
- `stimmo_http_request_duration_seconds` — Histogram, labels: `method`, `route`
- `stimmo_dossier_interest_total` — Counter, labels: `event` (`view`/`click`), `lang` (`it`/`en`). `view` is counted in the `estimate` handler when the interest button renders, `click` in `dossier_interest`; nothing else is labelled, so no identifier can reach it.
- `stimmo_share_events_total` — Counter, labels: `event` (`open`/`og_render`), `outcome` (`ok`/`invalid`/`error`). Incremented in the `share_view` and `og_image` handlers (not the dispatcher) because invalid/expired tokens are served as a normal 400/404 and would otherwise be indistinguishable from other failures in the HTTP counters.

Key invariants:

- **Instrumented at the dispatcher, not FastAPI.** `_dispatch` in `web/app.py` is wrapped by `metrics.instrument()`; this is the only place that sees every request including `/mcp`.
- **`route` label = `scope["endpoint"].__name__`**, not the raw path. For the `/mcp` branch a synthetic endpoint object is injected before dispatch. Never label by path — lang prefixes and dynamic segments would explode cardinality.
- **`/metrics` is NOT a FastAPI route.** It is served by `prometheus_client.start_http_server()` on a separate port (`:9100`), started in `server.py:main()` only when `STIMMO_METRICS_PORT` is set. This keeps it off the public Cloudflare tunnel.
- **Single process** — default global registry is correct. If `--workers` is ever added, switch to `prometheus_client` multiprocess mode.
- Grafana is accessible only via SSH tunnel (`ssh -L 3000:127.0.0.1:3000 <vps>`); Prometheus is internal to `stimmo-net` only.
