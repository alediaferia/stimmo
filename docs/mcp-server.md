# MCP server

stimmo exposes its Milan valuation pipeline as a remote [Model Context Protocol](https://modelcontextprotocol.io) server at **`https://stimmo.it/mcp`**. Any client that speaks MCP **Streamable HTTP** (Claude Desktop, the Claude.ai connector picker, MCP Inspector, custom SDK code) can:

1. Run a full Milan listing appraisal (`estimate_property`) in one call.
2. Run partial steps (geocode, zone lookup, OMI quote, amenity score, OMI history) without committing to a full estimate.
3. Estimate what buying costs on top of the price (`estimate_purchase_costs`): purchase taxes, notary, agency, mortgage costs and renovation, as sourced low/central/high lines.
4. Discover controlled vocabularies (property type, fine condition, energy class, etc.) and the current OMI semester as MCP resources, so an LLM doesn't have to guess valid enum values.

There is **no local stdio binary** — no `stimmo-mcp` script, no per-user install, no secrets. The server is the same uvicorn process that serves the web UI; the MCP transport is mounted at `/mcp`.

## Connecting

### Claude Desktop / Claude Code

Add to your MCP client config:

```json
{
  "mcpServers": {
    "stimmo": {
      "type": "http",
      "url": "https://stimmo.it/mcp"
    }
  }
}
```

### MCP Inspector

```sh
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP   URL: https://stimmo.it/mcp
```

### Local development

`uv run stimmo-web` mounts the same server at `http://127.0.0.1:8000/mcp`.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ uvicorn (stimmo-web → stimmo.web.app:application)           │
│                                                             │
│  Top-level ASGI dispatcher (web/app.py)                     │
│   ├── exact /mcp    → RateLimitMiddleware → FastMCP app     │
│   │                     └─ tools / resources / prompts      │
│   └── everything else  → FastAPI app                        │
│         ├── /{lang}/...      — existing HTML routes         │
│         └── /static/...      — bundled CSS/JS               │
└─────────────────────────────────────────────────────────────┘
```

Transport: **Streamable HTTP** (MCP spec ≥ 2025-03-26). Not stdio, not the deprecated HTTP+SSE transport. FastMCP is configured with `stateless_http=True`.

### Why a custom ASGI dispatcher, not `app.mount("/mcp", ...)`

Streamable HTTP is a single endpoint (`GET` for SSE, `POST` for JSON-RPC) — the spec defines no sub-paths under `/mcp`. Exact-match dispatch is therefore sufficient and forward-stable.

The reason we don't use `app.mount("/mcp", ...)`: Starlette's `Mount` is a prefix router that strips the matched prefix and forwards the remainder to the inner app. For a request to bare `/mcp`, the remainder is empty and Starlette issues a `307` redirect to `/mcp/`. Not every MCP client follows POST redirects (the spec doesn't require them to), so we'd be silently breaking some clients on session establishment. [web/app.py](../src/stimmo/web/app.py) sidesteps this with a 4-line top-level `application` callable that exact-matches `/mcp` and forwards the scope verbatim. The `stimmo-web` entry point ([web/server.py](../src/stimmo/web/server.py)) points uvicorn at `stimmo.web.app:application`.

The FastMCP session manager requires `.run()` from an async context, so it is started/stopped from the FastAPI `lifespan` and the underlying streamable app is built **once** ([mcp/server.py](../src/stimmo/mcp/server.py)) — the session manager is a stable singleton across the process lifetime.

## Module layout

```
src/stimmo/mcp/
├── server.py       # FastMCP() instance, build_mcp_app() factory
├── tools.py        # async tool functions; thin wrappers over engine/data/costs
├── resources.py    # enum vocab + semester + zones resources
├── prompts.py      # appraise_listing prompt template
├── ratelimit.py    # per-IP token-bucket ASGI middleware
└── cache.py        # InMemoryCache (LRU + TTL) behind a Cache protocol
```

## Tool surface

All tool I/O reuses the pydantic models in [models.py](../src/stimmo/models.py) — FastMCP derives JSON schemas from the pydantic types automatically. Every tool accepts an optional `lang: Literal["it", "en"] = "it"` argument.

### `estimate_property`

Full appraisal. Inputs are the [`Property`](../src/stimmo/models.py) shape; the server derives `omi_condition` from `fine_condition` via `derive_omi_condition`.

| Field | Required? | Default |
|---|---|---|
| `address`, `surface_m2`, `property_type`, `fine_condition`, `floor`, `total_floors`, `has_lift`, `asking_price_eur`, `construction_era` | yes | — |
| `energy_class` | no | `None` |
| `outdoor` | no | `Outdoor.NONE` |
| `has_box` | no | `false` |
| `orientation` | no | `Orientation.MIXED` |
| `exposure` | no | `Exposure.STREET` |
| `has_second_bathroom` | no | `false` |
| `room_count` | no | `None` |

Output: the full `Estimate` (band, multiplier, breakdown with `code` + `params` for client-side localisation, verdict, asking-vs-mid percentage).

### `lookup_omi_quote`

Inputs: `address`, `property_type`, and one of `omi_condition` / `fine_condition`. Output: `{omi_quote, zone_code, zone_descr, lat, lon}`. Lets a client fetch the band without running a full estimate.

### `geocode_milan_address`

Input: `address`. Output: `{lat, lon, normalized_address}` or structured error.

### `omi_zone_for_point`

Input: `lat`, `lon`. Output: `{zone_code, zone_descr}` or `outside_milan` error.

### `amenity_score`

Input: `lat`, `lon`. Output: `AmenityScore` (counts within 500 m / 500–1000 m, score percentage, items list).

### `omi_history`

Input: `zone_code`, `property_type`, optionally `condition`. Output: 8-semester `[{semester, eur_m2_min, eur_m2_max}]` from bundled [data/history.py](../src/stimmo/data/history.py) — no network calls.

### `estimate_purchase_costs`

What buying costs on top of the price. A pure calculation — no network, no geocoding — and a thin wrapper over [`costs/calculator.py`](../src/stimmo/costs/calculator.py); every rate lives in [`costs/rates.py`](../src/stimmo/costs/rates.py), not in the tool.

| Field | Required? | Default |
|---|---|---|
| `price_eur`, `prima_casa` | yes | — |
| `surface_m2` | no (required to price a renovation) | `0` |
| `seller` | no | `private` (`private` \| `builder_vat`) |
| `category` | no | `A/2` (A/1, A/8, A/9 cannot use the prima-casa regime) |
| `rendita_eur` | no | `None` — registro tax is then bounded, not estimated |
| `agency_pct` | no | `None` → 3 % + IVA (`0` = no agency) |
| `mortgage_eur` | no | `0` (an amount in €) |
| `mortgage_ltv_pct` | no | `None` — the mortgage as a percentage of the price, `0`–`100`; an alternative to `mortgage_eur` |
| `mortgage_years` | no | `None` → 25 (any whole number of years from 1 to 40; the web form offers 10, 15, 20, 25, 30) |
| `mortgage_rate_pct` | no | `None` — the nominal annual rate (TAN) in percent, `0`–`20`. Without it no payment is computed |
| `renovation_tier` | no | `None` → the default for `fine_condition`, else `none` |
| `fine_condition` | no | `None` |

Give the mortgage as `mortgage_eur` **or** `mortgage_ltv_pct`, not both (`mortgage_eur` left at `0` counts as not given). A percentage is turned into whole euros in one place (half-up, never above the price) and everything downstream — imposta sostitutiva, the mortgage-deed notary band, the down payment — uses that amount, so the two forms give the same breakdown. The percentage is computed on the **price**; the bank applies its limits to its own appraised value, which can be lower, so treat it as an approximation.

Output: a `CostBreakdown` — `lines[]` (each `{code, params, group, status, low_eur, central_eur, high_eur, source_url, as_of, due, stale}`; `group` is `closing`, `renovation` or `offset`), `notes[]` (each `{code, params, source_url}`; e.g. `rendita_missing`), `mortgage_eur` (the normalised amount), `mortgage_ltv_pct` (that amount as a share of the price), `down_payment_eur` (price − mortgage), and the `closing`, `renovation`, `total_on_top` and `cash_needed` ranges. `cash_needed` is `down_payment_eur` + `total_on_top`, so the cash splits into those two parts. The renovation-bonus lines are `offset` and are **not** subtracted from any total. `status` says how firm a line is: `statutory`, `contractual`, `benchmark`, `indicative` (not confirmed current, or no public source) or `needs_input`. `stale` is true when the line's benchmark range is past its review date (`due`, the date it was due; null on statutory and derived lines, which have no interval); it is independent of `status`, and one `ranges_overdue` note counts the stale lines. Lines carry `code` + `params` for client-side rendering, like the `Estimate` breakdown. Invalid input (e.g. a mortgage above the price, `mortgage_ltv_pct` above 100, or both `mortgage_eur` and `mortgage_ltv_pct`) returns `invalid_input`.

With a mortgage **and** a `mortgage_rate_pct`, the output also has `mortgage_payment` (otherwise it is `null`): `{years, months, rate_pct, monthly_payment_eur, total_interest_eur, total_repaid_eur, mortgage_costs, total_cost}`. The payment is the standard French amortisation, `P · i / (1 − (1 + i)^−n)` with `i = rate / 12` and `n = years × 12` (`P / n` at a 0 % rate): equal monthly instalments of principal and interest at a constant rate. `total_repaid_eur` is the exact instalment × `n` and `total_interest_eur` is that minus the loan. `mortgage_costs` is the range sum of the mortgage-related lines already in `closing` (imposta sostitutiva, mortgage-deed notary, istruttoria, perizia, insurance) and `total_cost` is the interest plus that range; the price is not in it. It is a generic calculation on the amount, term and rate the caller chooses: there is no affordability or income test, and no lender or product. The rate is never guessed — without one the note `mortgage_rate_missing` is returned instead (its `params` point at Banca d'Italia's current market average as a reference, not a default) — and with one, the note `mortgage_variable_rate` says the payment is computed at today's rate and will change if the rate is variable.

With a mortgage, `notes[]` also carries general information about the loan-to-value rules, never an assessment of the caller: `ltv_above_limit` (above the 80 % mutuo-fondiario limit of art. 38 TUB / CICR 22 April 1995, up to 100 % only with integrative guarantees), `consap_fund` (only for a prima-casa scenario, a loan up to €250,000, a category other than A/1, A/8, A/9, and an LTV above 80 %; the Consap Fondo Garanzia prima casa: reserved to priority categories, 50 % ordinary guarantee, and the two separate rules that raise it to 80 % (disability categories, ISEE up to €40,000; other categories, a loan above 80 % of the price including accessory costs and the same ISEE limit, for applications until 31/12/2027); the large families' ISEE figures are access limits and are not in `params`), `ltv_full_financing` (a loan equal to the price still leaves the taxes, notary, agency and other costs to be paid in cash) and `ltv_on_price_not_appraisal`.

### Error shape

Tools return `isError: true` with a structured payload — never raw Python exceptions:

```json
{ "code": "outside_milan" | "not_found" | "no_omi_quote" | "upstream_timeout" | "invalid_input" | "rate_limited",
  "message": "..." }
```

## Resources

| URI | Content |
|---|---|
| `stimmo://semester` | Current OMI semester string from `data.omi.semester()`. |
| `stimmo://vocab/property-type` | Enum values + localised hints from `PROPERTY_TYPE_HINTS`. |
| `stimmo://vocab/fine-condition` | `FineCondition` values + labels. |
| `stimmo://vocab/energy-class` | `A`–`G`. |
| `stimmo://vocab/outdoor` | `Outdoor` values + localised labels. |
| `stimmo://vocab/construction-era` | `ConstructionEra` values + localised labels. |
| `stimmo://vocab/orientation` | `Orientation` values + localised labels. |
| `stimmo://vocab/exposure` | `Exposure` values + localised labels. |
| `stimmo://vocab/seller-type` | `SellerType` values + localised labels (for `estimate_purchase_costs`). |
| `stimmo://vocab/cadastral-category` | `CadastralCategory` values + localised labels. |
| `stimmo://vocab/renovation-tier` | `RenovationTier` values + localised labels. |
| `stimmo://omi-zones` | `[{code, descr}]` for every Milano OMI zone. |

## Prompts

`appraise_listing(listing)` — guidance for converting a Milan listing URL or free-text description into a tool call. Instructs the model to default missing optional fields rather than asking the user.

## Rate limiting

Per-IP token bucket, enforced as ASGI middleware wrapping the MCP sub-app. Two tiers:

| Tier | Tools | Quota |
|---|---|---|
| Cheap | `geocode_milan_address`, `omi_zone_for_point`, `lookup_omi_quote`, `omi_history`, `estimate_purchase_costs`, all resources | 60 req/min/IP |
| Expensive | `estimate_property`, `amenity_score` | 10 req/min/IP |

Tier is determined by **peeking at the JSON-RPC body**: a `tools/call` whose `params.name` is in the expensive set draws from the expensive bucket. The body is buffered into memory and replayed to the downstream app via a wrapped `receive` callable (chunks reassembled, single `http.request` event with `more_body=False`).

Bucket keys are `CF-Connecting-IP` (set by Cloudflare; safe to trust because the VPS exposes no public ports — every request reaches uvicorn through the tunnel). If the header is missing, the middleware falls back to `scope['client']` and **logs a warning** — under normal traffic the header should always be present. `X-Forwarded-For` is deliberately ignored: in this topology it points at the cloudflared sidecar.

Exhaustion returns a JSON-RPC error envelope (`code: -32000, message: "rate_limited"`) with HTTP `429` and a `Retry-After` header.

## Caching

`InMemoryCache` ([mcp/cache.py](../src/stimmo/mcp/cache.py)) is an `OrderedDict`-backed LRU + per-entry TTL behind a `Cache` Protocol so a Redis impl can drop in later without touching tool code. The clock is injectable for deterministic tests.

| Source | Key | TTL | Max entries |
|---|---|---|---|
| Nominatim geocode | normalized address (lowercased, stripped) | 7 days | 5000 |
| Overpass amenities | `(lat, lon)` rounded to 4 dp (~11 m at Milan's latitude) | 24 hours | 5000 |
| OMI lookup | `(zone, property_type, condition)` | forever (bundled CSV) | unbounded |
| OMI history | `(zone, property_type)` | forever | unbounded |

## i18n

- Per-call `lang: Literal["it", "en"]` argument on every tool (default `it`).
- Tool execution is wrapped in `use_locale(lang)` from [i18n.py](../src/stimmo/i18n.py) so `gettext_lazy` strings in the breakdown resolve in the caller's language.
- Vocab resources localise their labels via the same mechanism.
- The `stimmo_lang` cookie used by the web UI is **not** read by the MCP server.

## Deployment topology

stimmo.it runs as a Docker Compose stack on a single Hetzner VPS:

```
Internet → Cloudflare edge (TLS, WAF) → Cloudflare Tunnel (cloudflared sidecar) → stimmo:8000 (uvicorn, plain HTTP, docker bridge)
```

- The tunnel routes all of `stimmo.it/*` to `stimmo:8000`, so `/mcp` is reachable with no tunnel/ingress edits.
- One stimmo container, no horizontal scale. In-process rate-limit buckets and TTL caches are correct for this topology; a Redis-backed impl would be required before adding a second replica.
- Cloudflare's idle timeout (~100 s) applies to streaming responses. FastMCP's Streamable HTTP transport keeps SSE connections alive with periodic events well below that bound — do not disable those keepalives.

## CORS

`/mcp` is same-origin with the web UI (both served from `stimmo.it` via the same tunnel), so no CORS config is needed. If the MCP endpoint ever moves to a subdomain (e.g. `mcp.stimmo.it`), revisit: that requires both a tunnel hostname mapping and explicit CORS headers in the app.

## Testing

```sh
uv run pytest tests/test_mcp_*    # MCP-specific
uv run pytest                     # everything
```

The suite is structured as:

- `test_mcp_cache.py` — TTL expiry and LRU eviction with an injected clock (no real sleeps).
- `test_mcp_ratelimit.py` — tier separation, per-IP isolation, `CF-Connecting-IP` precedence over `X-Forwarded-For`, `scope['client']` fallback, token refill.
- `test_mcp_protocol.py` — drives the mounted app via `httpx.ASGITransport` + a real `mcp.client` session. Catches schema-generation bugs that pure unit tests miss (`tools/list`, `resources/list`, `prompts/get`, structured errors).
- `test_mcp_estimate_route.py` — one full happy-path end-to-end through the ASGI dispatcher with monkeypatched Nominatim + Overpass.
- `test_mcp_i18n.py` — `lang` argument, `Accept-Language` fallback, unsupported lang falls back to `it`.
- `test_mcp_costs.py` — `estimate_purchase_costs` through the endpoint (worked example, bounded registro, default renovation tier, the mortgage payment fields and their rate/term validation, structured errors), its vocab resources and its cheap rate-limit tier.

Upstream calls (Nominatim, Overpass) are monkeypatched in tests; OMI / zone / history lookups hit the bundled deterministic data.

## Future work

- **API keys + per-key rate limits.** Hook in once anonymous abuse becomes visible or a partner needs a higher quota. Schema would be `Authorization: Bearer <key>`; per-IP middleware stays as a backstop.
- **Redis-backed cache + rate limiter.** Swap the `InMemoryCache` impl behind the `Cache` protocol; same for the bucket store. No tool code changes.
- **`parse_immobiliare_listing` tool.** Promote the existing importer ([data/importers/immobiliare.py](../src/stimmo/data/importers/immobiliare.py)) to an MCP tool so `appraise_listing` becomes a one-call URL → estimate. The expensive-tier set in [ratelimit.py](../src/stimmo/mcp/ratelimit.py) already anticipates this name.
- **OAuth 2.0 with dynamic client registration.** Required if Claude.ai connector listing demands it.
- **Per-tool observability.** Structured logs with `tool_name`, `lang`, `cache_hit`, `upstream_latency_ms`, `ip_bucket_remaining`.
