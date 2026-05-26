# MCP server

stimmo exposes its Milan valuation pipeline as a remote [Model Context Protocol](https://modelcontextprotocol.io) server at **`https://stimmo.it/mcp`**. Any client that speaks MCP **Streamable HTTP** (Claude Desktop, the Claude.ai connector picker, MCP Inspector, custom SDK code) can:

1. Run a full Milan listing appraisal (`estimate_property`) in one call.
2. Run partial steps (geocode, zone lookup, OMI quote, amenity score, OMI history) without committing to a full estimate.
3. Discover controlled vocabularies (property type, fine condition, energy class, etc.) and the current OMI semester as MCP resources, so an LLM doesn't have to guess valid enum values.

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
├── tools.py        # async tool functions; thin wrappers over engine/data
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
| `stimmo://omi-zones` | `[{code, descr}]` for every Milano OMI zone. |

## Prompts

`appraise_listing(listing)` — guidance for converting a Milan listing URL or free-text description into a tool call. Instructs the model to default missing optional fields rather than asking the user.

## Rate limiting

Per-IP token bucket, enforced as ASGI middleware wrapping the MCP sub-app. Two tiers:

| Tier | Tools | Quota |
|---|---|---|
| Cheap | `geocode_milan_address`, `omi_zone_for_point`, `lookup_omi_quote`, `omi_history`, all resources | 60 req/min/IP |
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

Upstream calls (Nominatim, Overpass) are monkeypatched in tests; OMI / zone / history lookups hit the bundled deterministic data.

## Future work

- **API keys + per-key rate limits.** Hook in once anonymous abuse becomes visible or a partner needs a higher quota. Schema would be `Authorization: Bearer <key>`; per-IP middleware stays as a backstop.
- **Redis-backed cache + rate limiter.** Swap the `InMemoryCache` impl behind the `Cache` protocol; same for the bucket store. No tool code changes.
- **`parse_immobiliare_listing` tool.** Promote the existing importer ([data/importers/immobiliare.py](../src/stimmo/data/importers/immobiliare.py)) to an MCP tool so `appraise_listing` becomes a one-call URL → estimate. The expensive-tier set in [ratelimit.py](../src/stimmo/mcp/ratelimit.py) already anticipates this name.
- **OAuth 2.0 with dynamic client registration.** Required if Claude.ai connector listing demands it.
- **Per-tool observability.** Structured logs with `tool_name`, `lang`, `cache_hit`, `upstream_latency_ms`, `ip_bucket_remaining`.
