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

### MCP server

The `stimmo/mcp/` package exposes the valuation pipeline as a remote MCP server (Streamable HTTP) at `/mcp`. See [docs/mcp-server.md](docs/mcp-server.md) for the full reference. Key invariants:

- **No `app.mount("/mcp", ...)`.** Streamable HTTP is a single endpoint with no sub-paths, so exact-match dispatch is sufficient. We avoid `Mount` because it strips the prefix and 307-redirects bare `/mcp` to `/mcp/`, which not every MCP client follows on POST. `web/app.py` defines a top-level `application` ASGI callable that exact-matches `/mcp` to the MCP sub-app and routes everything else to the FastAPI `app`. `stimmo-web` runs uvicorn against `stimmo.web.app:application`, not `:app`.
- **Session manager started in the FastAPI lifespan.** `FastMCP.streamable_http_app()` is built once at import time so the session manager is a stable singleton; `_lifespan` drives `session_manager.run()`.
- **Client IP is `CF-Connecting-IP`.** `mcp/ratelimit.py` reads this header (Cloudflare-set, unspoofable because the VPS exposes no public ports). Do not read `X-Forwarded-For` — in this topology it points at the cloudflared sidecar.
- **No new pricing logic in tools.** `mcp/tools.py` is a thin async wrapper over `data/` and `valuation/engine.py`. Multipliers stay in `valuation/adjustments.py`.
- **Cache + rate-limit interfaces.** `InMemoryCache` is behind a `Cache` protocol and the rate limiter uses an in-process dict — both designed to swap to Redis without touching tool code if a second replica is ever added.

### i18n

All routes are prefixed `/{lang}/` (`it` or `en`). Locale negotiation order: `stimmo_lang` cookie → `Accept-Language` header → `it_IT` default.

- **`stimmo/i18n.py`** — ContextVar-backed `gettext`/`ngettext`, `use_locale()` context manager, `fmt_eur`/`fmt_pct`/`fmt_semester` formatters, `negotiate_locale`.
- **`stimmo/locale/`** — Babel catalog. `it_IT` has full Italian translations; `en_US` uses msgid (English) as-is. Run `pybabel compile` after editing `.po` files.
- **`babel.cfg`** — extraction config for Python source and Jinja2 templates (`jinja2.ext.i18n`).
- **`web/labels.py`** — renders `AdjustmentBreakdown` (structured `code`/`params`) to translated strings. `AdjustmentBreakdown.name` no longer exists; use `.code` to identify entries.
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

SEO strategy, Search Console tooling and competitive research live in the private
`stimmo-growth` repo — deliberately not here. Keep strategy, keyword research and traffic data out
of this repository.

### Observability

`web/metrics.py` wraps the top-level ASGI dispatcher with a Prometheus middleware. Collectors:

- `stimmo_http_requests_total` — Counter, labels: `method`, `route`, `status`
- `stimmo_http_request_duration_seconds` — Histogram, labels: `method`, `route`
- `stimmo_share_events_total` — Counter, labels: `event` (`open`/`og_render`), `outcome` (`ok`/`invalid`/`error`). Incremented in the `share_view` and `og_image` handlers (not the dispatcher) because invalid/expired tokens are served as a normal 400/404 and would otherwise be indistinguishable from other failures in the HTTP counters.

Key invariants:

- **Instrumented at the dispatcher, not FastAPI.** `_dispatch` in `web/app.py` is wrapped by `metrics.instrument()`; this is the only place that sees every request including `/mcp`.
- **`route` label = `scope["endpoint"].__name__`**, not the raw path. For the `/mcp` branch a synthetic endpoint object is injected before dispatch. Never label by path — lang prefixes and dynamic segments would explode cardinality.
- **`/metrics` is NOT a FastAPI route.** It is served by `prometheus_client.start_http_server()` on a separate port (`:9100`), started in `server.py:main()` only when `STIMMO_METRICS_PORT` is set. This keeps it off the public Cloudflare tunnel.
- **Single process** — default global registry is correct. If `--workers` is ever added, switch to `prometheus_client` multiprocess mode.
- Grafana is accessible only via SSH tunnel (`ssh -L 3000:127.0.0.1:3000 <vps>`); Prometheus is internal to `stimmo-net` only.
