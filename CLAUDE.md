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
- **Committing.** When the user asks to commit pending changes, use the `git-commit-curator` subagent (Agent tool with `subagent_type: "git-commit-curator"`); do not commit directly with Bash. Commits follow strict Conventional Commits — see `CONTRIBUTING.md`.
- **CI/CD.** For pipeline-specific work (debugging CI failures, extending GitHub Actions), use the `github-ci-pipeline-maintainer` subagent.

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

### Observability

`web/metrics.py` wraps the top-level ASGI dispatcher with a Prometheus middleware. Collectors:

- `stimmo_http_requests_total` — Counter, labels: `method`, `route`, `status`
- `stimmo_http_request_duration_seconds` — Histogram, labels: `method`, `route`

Key invariants:

- **Instrumented at the dispatcher, not FastAPI.** `_dispatch` in `web/app.py` is wrapped by `metrics.instrument()`; this is the only place that sees every request including `/mcp`.
- **`route` label = `scope["endpoint"].__name__`**, not the raw path. For the `/mcp` branch a synthetic endpoint object is injected before dispatch. Never label by path — lang prefixes and dynamic segments would explode cardinality.
- **`/metrics` is NOT a FastAPI route.** It is served by `prometheus_client.start_http_server()` on a separate port (`:9100`), started in `server.py:main()` only when `STIMMO_METRICS_PORT` is set. This keeps it off the public Cloudflare tunnel.
- **Single process** — default global registry is correct. If `--workers` is ever added, switch to `prometheus_client` multiprocess mode.
- Grafana is accessible only via SSH tunnel (`ssh -L 3000:127.0.0.1:3000 <vps>`); Prometheus is internal to `stimmo-net` only.
