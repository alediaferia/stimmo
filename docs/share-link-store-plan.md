# Implementation plan: short share links via a self-hosted SQLite store

> **Status:** SHIPPED (verified live 2026-07-05). This is a
> self-contained handoff doc for a future implementation session that may have **no memory
> of the discussion that produced it**. Everything needed to execute is below: settled decisions (with the
> *why*), rejected alternatives, schema, interfaces, file/line anchors, a sequenced step
> plan, open questions, and acceptance criteria. **Do not re-litigate the settled
> decisions.** An optional, independent enhancement — embedding a map snapshot in the OG
> image — is tracked separately in [`docs/share-link-og-map-snapshot.md`](./share-link-og-map-snapshot.md)
> and is **out of scope for this plan**.
>
> Owner of code steps: `stimmo-maintainer`. Deploy/compose: `github-ci-pipeline-maintainer`.
> Final validation: `stimmo-e2e-validator`.

---

## Implementation status (updated 2026-06-22 — READ THIS FIRST on resume)

**DONE — steps 0–6 (code), committed on local `main`, NOT pushed:**

| Commit | Subject |
|--------|---------|
| `e752680` | feat(web): add SQLite share-link blob store |
| `3352d33` | chore: add var/ to gitignore |
| `3e95e71` | refactor(share): extract payload/blob compression primitives |
| `3f53c9c` | feat(web): wire share-link store into app (short URLs, dual-path resolve) |
| `565a17b` | test(share): verify marker rendering on short-link shared view |
| `d1391f7` | feat(web): async amenity name enrichment on shared short-link views |
| `3268b19` | feat(data): add TTL+LRU cache for fetch_amenities |

- Full suite green (**248 tests**, ruff clean). Defaults adopted: id length **N=8**;
  `STIMMO_SHARE_DB` unset → repo-local **`var/share.db`** (gitignored); `last_seen` bumped
  every read; legacy `encode` stays marker-free; legacy `/{lang}/s/{token}` route retained.
- **Local e2e PASSED** (real Immobiliare listing *Via Lomazzo 4, rif. 129046362*, via Claude
  in Chrome): import→estimate (zone C16, verdict fair), short link `/s/<id>` (8-char),
  12 amenity markers render, async enrichment patched **12/12** tooltips to real names,
  locale negotiation (en/it), OG PNG 200, legacy long token resolves. (IntersectionObserver
  auto-fire needs a foreground tab — expected; logic verified by direct invocation.)
- Step 9 (binary codec) intentionally NOT done (optional/deferred, D10).

**REMAINING — the storage migration + production rollout:**

1. **Step 7a (repo — `github-ci-pipeline-maintainer`, NOT YET DONE — was interrupted):** edit
   `deploy/docker-compose.yml` `stimmo` service to add env `STIMMO_SHARE_DB=/data/share.db`
   and volume `/opt/stimmo/share-data:/data` (host bind mount, matching the
   prometheus/grafana convention), and document the backup. Commit (keep local).
2. **Step 7b (VPS — repo owner):** `mkdir -p /opt/stimmo/share-data`; make it writable by the
   container's non-root `stimmo` user (image runs `USER stimmo` from `useradd -r`; find UID
   via `docker compose exec stimmo id -u`, then `chown`). Mirror the same env+volume into
   `/opt/stimmo/docker-compose.yml` (CI only rewrites the `image:` line — it never copies the
   repo compose). `docker compose up -d` with the CURRENT image first (harmless — proves the
   mount/permissions). Add a WAL-safe backup cron:
   `sqlite3 /opt/stimmo/share-data/share.db ".backup /opt/stimmo/backups/share-$(date +%F).db"`.
3. **Push + co-deploy (D8 — ORDERING CRITICAL):** push the 7 commits to `main` (deploy is
   **tag-gated** via `release.yml` on `v[0-9]+.[0-9]+.[0-9]+`, so pushing `main` only runs CI,
   never deploys). The VPS volume (7b) MUST exist before the version tag ships the new image,
   else new `/s/<id>` links write to the ephemeral container layer and are wiped on the next
   `compose pull`. Then `cz bump` → push the `vX.Y.Z` tag → `release.yml` builds + SSH-deploys.
4. **Step 8 (prod e2e — `stimmo-e2e-validator`):** after deploy, validate against
   https://stimmo.it including durability (a link created pre-`compose pull` still resolves
   post-pull).

**Separate / optional (found during e2e, NOT part of this plan):** pre-existing importer
prefill gaps in `data/importers/immobiliare.py` — `address` not populated, `outdoor=none`
despite a balcony (affects the estimate), `energy_class` unknown despite class F,
`total_floors=5` vs listing "3 piani". Track/fix independently.

**To resume:** continue this conversation OR start fresh — this section + the rest of this doc
+ the `share-link-store-design` memory carry everything needed. A local dev server may still
be running (was PID 4677). Browser-driven listing work is captured in the
`immobiliare-chrome` skill (committed: `2b09f91`).

---

## 1. Problem statement

stimmo lets a user share an estimate by URL. Two coupled problems:

1. **Share URLs are far too long.** The current link embeds the *entire* state in the URL
   as a base64url token (~420–480 chars). It renders badly raw in WhatsApp / Telegram /
   SMS / print / QR codes. (X rewrites to t.co, but you cannot rely on that.)
2. **Amenity markers don't render on shared-estimate maps.** The encoder drops
   `items_within_500m` to keep the URL small, so shared views show the map with the
   property dot but no amenity dots — a visible regression vs. a freshly-computed result.

(A third idea — a map snapshot in the OG preview image — is an optional enhancement tracked
in [`docs/share-link-og-map-snapshot.md`](./share-link-og-map-snapshot.md), out of scope here.)

### Why a short link *requires* a store (information-theoretic floor)

The current token **is** the state, so its length has a hard floor. Even with an optimal
schema-aware bit-packed binary codec and **no** amenity markers/names, the payload floor
is ~30–40 chars; *with* markers it is ~250 chars. Neither is "short" for the channels
above. A genuinely short link (`stimmo.it/s/<8 chars>`, ~20 chars total) is only possible
with **indirection**: a short key that points at state stored server-side. That store is
the subject of this plan.

---

## 2. Settled decisions (do not re-argue)

| # | Decision | Why |
|---|----------|-----|
| D1 | **Build our own SQLite store**, content-addressed. | Short links need indirection. SQLite is zero-infra, single-file, fits the single-process / single-VPS topology. |
| D2 | **No external URL shortener.** | Leaks every shared listing+price to a third party; link-rot outside our control; violates stimmo's self-contained ethos. |
| D3 | **Content-addressed IDs:** `id = base62(sha256(blob))[:N]`, `INSERT OR IGNORE`. | Idempotent dedup — sharing the same estimate twice yields the same short link, no duplicate rows, no sequence counter to coordinate. |
| D4 | **Short URL `stimmo.it/s/<id>`, drop the `/{lang}/` prefix from the share path.** Negotiate locale on load (cookie → Accept-Language → it_IT). OG becomes `/og/<id>.png` (already lang-free). | A lang prefix would eat ~3 chars of the budget and isn't needed: the recipient's own locale is the right one to show, not the sharer's. |
| D5 | **Dual-path reads / indefinite back-compat.** New links are store IDs; the **legacy self-contained token decoder is retained indefinitely** so existing long links keep resolving. A resolve helper tries store-by-id first, then legacy `share.decode`. | Existing shared links are already in the wild (WhatsApp history, bookmarks). Breaking them is unacceptable; the legacy decoder is ~70 lines and cheap to keep. |
| D6 | **Amenity names resolved ASYNCHRONOUSLY (explicit user requirement).** Store marker **positions + kinds** only. After the shared page loads, fetch named items live via the existing `/api/amenities` and patch tooltips by nearest-coordinate match. | Markers render instantly and deterministically (consistent with the counts that drove the estimate); names are cosmetic and never affect the estimate, so live/snapshot drift is harmless. Keeps the stored blob tiny. |
| D7 | **GC deferred.** Add `created_at` / `last_seen` columns now; defer the GC job and retention window `N`. | We don't yet know real usage/retention needs; adding columns now is free, building/ tuning GC prematurely is waste. Leave a clear hook. |
| D8 | **Compose volume is required and ordering-critical.** Add a persistent `/data` mount + `STIMMO_SHARE_DB` env to the `stimmo` service; it **MUST** co-deploy with the write-path change or new links die on the next `docker compose pull`. | The `stimmo` service currently has **no** persistent storage; without the mount the SQLite file lives in the ephemeral container layer and is wiped on every image pull. User accepted the durability trade-off (DB-backed new links; backups mitigate). |
| D9 | **Add a TTL cache to `fetch_amenities`** keyed by quantized lat/lon. | Async name enrichment (D6) means every shared-page open hits Overpass. The cache bounds per-open Overpass load and protects a rate-limited upstream. |
| D10 | **Schema-aware binary codec is DEMOTED to optional polish** — useful only to shrink the *retained legacy fallback token*, not load-bearing for the primary short-link UX. | Once short links exist, the legacy token is a fallback path; shrinking it is nice-to-have, not required. Mark clearly deferrable. |

### Rejected alternatives (with reasons — do not revisit)

- **Keep stateless in-URL token, just compress/bit-pack harder.** Rejected: information-
  theoretic floor (~30–40 chars without markers, ~250 with) still displays badly raw; the
  token *is* the state, so no codec makes it "short". (See §1.)
- **External URL shortener (bit.ly, etc.).** Rejected — D2: privacy leak, link-rot,
  against self-contained ethos.
- **Store amenity names in the blob.** Rejected — D6: user explicitly wants names fetched
  live; storing them bloats the blob and risks stale names with no upside (names are
  cosmetic).
- **Auto-increment / sequence IDs.** Rejected in favour of content-addressing (D3):
  content-addressing gives free idempotent dedup and no shared-counter coordination.

---

## 3. Current implementation (verified 2026-06-20 — anchors for the implementer)

- **`src/stimmo/web/share.py`** — stateless token codec.
  - `encode(prop, lat, lon, amenity) -> str` (line 40): payload =
    `{v, p: Property.model_dump, lat: round6, lon: round6, a: AmenityScore dump}`.
    `share.py:47` excludes `items_within_500m` (`exclude={"items_within_500m"}`) → this is
    the direct cause of bug #2. Format: `b'\x01' + zlib(level=9) → base64url no padding`,
    ~420–480 chars.
  - `decode(token) -> tuple[Property, float, float, AmenityScore]` (line 55) — raises
    `ShareTokenError`. `_VERSION = b'\x01'`, `_MAX_DECOMPRESSED = 64_000` zip-bomb guard.
- **`src/stimmo/web/app.py`**
  - `_build_result_context` write path (lines 545–549): `token = _share.encode(...)`,
    `share_url = f"{base}/{lang}/s/{token}"`, `og_image_url = f"{base}/og/{token}.png"`.
  - `share_view` — `GET /{lang}/s/{token}` (line 695): `_set_locale`, `_share.decode`,
    zone lookup, OMI lookup, `_build_result_context`, sets `ctx["is_shared_view"]=True`,
    increments `_metrics.SHARE_EVENTS.labels(event="open", outcome=...)`.
  - `og_image` — `GET /og/{token}.png` (line 728, **no lang prefix**, fetched by social
    crawlers): decode → zone → OMI → `engine.estimate` → `_ogimage.render(est, prop)` →
    PNG with `Cache-Control: public, max-age=86400, immutable`. Increments
    `SHARE_EVENTS(event="og_render", ...)`.
  - `api_amenities` — `GET /{lang}/api/amenities?lat&lon` (line 762): returns
    `score.model_dump()` **including** `items_within_500m` (names + coords). **A client can
    already fetch named items with no new endpoint** — this is the backbone of async
    enrichment (D6).
- **`src/stimmo/web/templates/result.html:426`** — map-marker block gated on
  `{% if est.amenity_score.items_within_500m %}`; the per-item loop (lines 453–462) draws
  `L.circleMarker([item.lat, item.lon])` with `kindColors` and
  `bindTooltip((item.name or item.kind))`. Empty on shared views → no markers (bug #2).
  The property-dot + tile layers are inside the same `{% if %}`, so today a marker-less
  shared view renders **no map at all**.
- **`src/stimmo/data/amenities.py:176`** — `fetch_amenities(lat, lon) -> AmenityScore`
  has **NO caching**; hits Overpass directly (`_query`, one retry). Returns counts +
  `score_pct` + `items_within_500m=_extract_items(data_500)` (capped at 12 items, line
  163).
- **`src/stimmo/models.py`** — `Property` (16 fields, line ~131); `AmenityItem`
  `{kind: Literal[...], name: str|None, lat, lon}` (line 161); `AmenityScore` (line 168):
  12 counters + `score_pct` + `items_within_500m: list[AmenityItem]`.
- **`src/stimmo/web/ogimage.py`** — `render(est, prop) -> bytes`, server-side Pillow,
  1200×630, text-only today.
- **i18n** — all routes `/{lang}/`; `stimmo/i18n.py negotiate_locale` (cookie →
  Accept-Language → it_IT). `/og/{token}.png` already lang-free.
- **Deployment** — `deploy/docker-compose.yml`: `stimmo` service has **NO volume** (only
  `prometheus` and `grafana` mount `/opt/stimmo/prometheus-data` and
  `/opt/stimmo/grafana-data` as **host bind mounts**, not named volumes). Single process,
  Cloudflare Tunnel, client IP `CF-Connecting-IP`, no public ports.
- **Metrics** — `stimmo_share_events_total{event,outcome}` counter, incremented in
  `share_view` / `og_image` (see CLAUDE.md "Observability").

---

## 4. Invariants touched — and why none are violated

- **"Data is bundled, not fetched at runtime."** The SQLite store holds **user-generated
  share snapshots**, not reference data. Reference data (OMI, zones, history) stays
  bundled. No new reference data is fetched at runtime. ✅ Not violated.
- **"`adjustments.py` is the only tuning surface."** No multiplier logic added anywhere;
  the estimate is still recomputed by `engine.estimate` from bundled data on read. ✅
- **"OMI band is the spine; no comparable-listings."** Unchanged. ✅
- **"Single-process topology."** SQLite with WAL is fine for one process. The store sits
  behind a narrow interface (like `Cache`/rate-limit protocols) so a future Redis/Postgres
  swap stays local if a second replica is ever added. ✅
- **"Milano comune only."** Resolve path still does point-in-polygon → out-of-comune
  shares still error. ✅

**New, accepted trade-off (D8):** new short links depend on a writable DB file surviving
deploys. Mitigation: persistent host bind mount + backups. Old long links remain
fully stateless (D5).

---

## 5. Design detail

### 5.1 SQLite schema + DDL

One table. Store the **already-compressed** blob (the same `zlib`-compressed payload the
legacy codec produces, *without* the version byte — see 5.3), so the store is codec-
agnostic and the legacy decoder can be reused on read.

```sql
CREATE TABLE IF NOT EXISTS shares (
    id          TEXT PRIMARY KEY,   -- base62(sha256(blob))[:N]
    blob        BLOB NOT NULL,      -- zlib-compressed payload (see 5.3)
    created_at  INTEGER NOT NULL,   -- unix epoch seconds, first insert
    last_seen   INTEGER NOT NULL    -- unix epoch seconds, bumped on read (GC hook, D7)
) STRICT;
```

Connection pragmas at open: `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=3000;`. WAL gives safe concurrent readers + one writer (sufficient for
single-process async with a thread pool).

> **`last_seen` write-amplification note:** bumping `last_seen` on every read is a write
> per page view. Acceptable at stimmo's volume. If it ever matters, throttle (only bump if
> `now - last_seen > 3600`). Implementer's call; default to the simple version.

### 5.2 ID scheme + collision reasoning — choose N

`id = base62(sha256(blob))[:N]`.

- base62 alphabet `[0-9A-Za-z]`; URL-safe, denser than hex, no padding.
- **N = 8** recommended → 62^8 ≈ 2.18×10^14 keyspace.
  - Birthday-bound: ~50% collision probability needs ~√(62^8) ≈ 1.5×10^7 ≈ **15 million**
    distinct shares. stimmo will not approach this; expected lifetime distinct shares are
    in the thousands–low-millions at most.
  - At 100k distinct shares, collision probability ≈ 100000² / (2·62^8) ≈ **2.3×10^-5**.
- **Collisions are content-addressed, so a collision = two *different* blobs hashing to the
  same 8-char prefix.** Handle defensively: `INSERT OR IGNORE`; then **read back the stored
  blob and compare to the one you tried to insert**. If they differ (true prefix
  collision), fall back to N+2 chars for that blob (store under the longer id) — extremely
  rare, keeps correctness exact. Document this branch; it should essentially never fire.
- Final N is an open question only in the sense of "confirm 8 at implementation"; default
  to 8.

### 5.3 Blob format

Reuse the legacy payload shape so one decoder serves both paths. Define a shared helper:

```python
# share.py — refactor the existing encode/decode into payload <-> blob primitives
def _payload_dict(prop, lat, lon, amenity) -> dict: ...        # the dict at share.py:42
def _compress_payload(payload: dict) -> bytes: ...             # zlib(level=9) of compact json
def _decompress_payload(blob: bytes) -> dict: ...              # inverse, with size guard

# Legacy token = b'\x01' + blob, base64url.  Store blob = the SAME `blob` (no version byte).
```

**New: include amenity marker positions+kinds in the blob.** Today `encode` excludes
`items_within_500m` (share.py:47). For the store path, include a *minimal* marker list —
**kind + lat + lon only, names omitted** (D6). Add a payload key, e.g.
`"m": [[kind_code, lat6, lon6], ...]`, where `kind_code` is the index into the fixed kind
order `["metro","tram","park","supermarket","school","pharmacy"]` (1 byte vs a string).
Cap at 12 (matches `_extract_items`). This fixes bug #2 for new links while keeping the
blob tiny (~12 × ~12 bytes pre-compression).

> The legacy long-token `encode` may **also** start including `"m"` so freshly-minted
> *fallback* tokens render markers too — but this is optional and grows the legacy token;
> since new links go through the store, prefer adding `"m"` only to the stored blob and
> leave legacy `encode` minimal. Implementer's call.

### 5.4 New module: `src/stimmo/web/share_store.py`

A narrow, swappable interface (mirrors the `Cache` protocol pattern).

```python
class ShareStore(Protocol):
    def put(self, blob: bytes) -> str: ...           # returns id; idempotent (D3)
    def get(self, id: str) -> bytes | None: ...      # bumps last_seen; None if absent

class SqliteShareStore:
    def __init__(self, path: str | Path, *, id_len: int = 8, clock=time.time) -> None: ...
    def put(self, blob: bytes) -> str: ...
    def get(self, id: str) -> bytes | None: ...
    # internal: _connect() -> sqlite3.Connection with pragmas; _id_for(blob) -> str
```

- Constructed once at app import/lifespan from `STIMMO_SHARE_DB` env (path). If the env is
  unset (local dev/tests without a path), default to a temp/`:memory:` path or a repo-local
  `var/share.db` — pick one and document; tests should be able to inject an in-memory or
  tmp_path store via the constructor.
- `sqlite3` calls are sync; in the async web handlers run them via
  `anyio.to_thread.run_sync` (anyio is already a dep) to avoid blocking the event loop.
  Alternatively keep handlers sync (the share routes are currently `def`, not `async def`)
  — if so, direct sync calls are fine. **Match the existing handler style**: `share_view`
  and `og_image` are sync `def`, so plain sync sqlite calls are acceptable; no thread
  offload needed unless they're converted to async.

### 5.5 `share.py` changed/added interfaces

Keep `encode`/`decode` working (legacy). Add store-aware helpers:

```python
# unchanged signatures, retained indefinitely (D5):
def encode(prop, lat, lon, amenity) -> str            # legacy long token
def decode(token: str) -> tuple[Property, float, float, AmenityScore]

# new:
def encode_blob(prop, lat, lon, amenity) -> bytes     # the stored blob (5.3), includes "m"
def decode_blob(blob: bytes) -> tuple[Property, float, float, list[AmenityItem]_or_AmenityScore]
def resolve(identifier: str, store: ShareStore) -> tuple[Property, float, float, AmenityScore]:
    """Dual-path (D5): if identifier looks like a store id (len<=~10, base62) try
    store.get(); on hit decode_blob. Otherwise (or on miss) fall back to legacy decode().
    Raises ShareTokenError if neither resolves."""
```

> **Disambiguation:** a store id is short base62 (`[0-9A-Za-z]{<=10}`); a legacy token is
> long base64url (contains `-`/`_` or is ≫ 10 chars). Distinguish by length+charset, but
> **always fall back to legacy `decode` on store miss** so a short-but-legacy edge case
> still resolves. Order: try store by id → on miss/format-mismatch, try `decode`.

### 5.6 `app.py` changes

- **Write path** (`_build_result_context`, lines 545–549):
  ```python
  blob = _share.encode_blob(prop, lat, lon, amen)
  share_id = _share_store.put(blob)              # NEW
  base = str(request.base_url).rstrip("/")
  share_url = f"{base}/s/{share_id}"             # NEW: no /{lang}/ (D4)
  og_image_url = f"{base}/og/{share_id}.png"     # NEW: id, not token
  ```
  Keep returning `token`/`share_url`/`og_image_url` keys for the template.
- **New route** `GET /s/{id}` (lang-free, D4):
  ```python
  @app.get("/s/{id}", response_class=HTMLResponse)
  def share_view_short(request: Request, id: str = FPath(min_length=1)) -> HTMLResponse:
      lang = negotiate_locale(request)           # cookie -> Accept-Language -> it_IT
      _set_locale(request, lang)
      try:
          prop, lat, lon, amen = _share.resolve(id, _share_store)
      except _share.ShareTokenError:
          ... SHARE_EVENTS(event="open", outcome="invalid"); return _error(...)
      # then identical zone/OMI/_build_result_context/is_shared_view path as share_view
  ```
- **Retain** the legacy `GET /{lang}/s/{token}` route (`share_view`, line 695)
  **unchanged** — old links keep working. (It can optionally route through `resolve` too,
  but keeping it on `decode` is simplest and lowest-risk.)
- **OG route**: add `GET /og/{id}.png` resolving via `resolve` (or make the existing
  `og_image` at line 728 call `resolve` so a single route handles both ids and legacy
  tokens — preferred, since the path pattern `{token}.png` already matches both).
- **`_build_result_context` must pass marker data to the template for shared views.** On
  the short share path, `amen` returned by `resolve`/`decode_blob` will carry the stored
  markers (kinds+coords, no names). Ensure `est.amenity_score.items_within_500m` is
  populated from them so the template `{% if %}` at result.html:426 fires. (This alone
  fixes bug #2 — the async enrichment in 5.7 only adds names.)

### 5.7 Template + async name-enrichment client flow (D6, bug #2)

**result.html:426–465.**

1. Marker block now fires on shared views because `items_within_500m` is populated with
   kind+coords (names null). Tooltips show `(item.name or item.kind)` → the **kind** label
   initially. This already fixes the "no markers" regression.
2. Add a lazy async enrichment script, gated on `is_shared_view` (or simply "names are
   missing"), that:
   - Triggers **on map visible** (IntersectionObserver on `#amenity-map`) to avoid an
     Overpass hit for users who never scroll to the map.
   - `fetch('/{{ lang }}/api/amenities?lat={{ lat }}&lon={{ lon }}')` — **existing
     endpoint**, returns `items_within_500m` with names+coords.
   - For each returned named item, find the already-drawn marker whose stored coord is
     nearest within a tolerance and `setTooltipContent(name)`.
     - **Matching tolerance:** match by same `kind` AND haversine/coordinate distance
       `< ~30 m` (coords are rounded to 6dp ≈ 0.11 m, but Overpass may return a slightly
       different node for the same POI across queries). Use nearest-within-tolerance,
       one-to-one (mark a returned item consumed once matched) to avoid double-assigning.
   - **Graceful degradation:** if the fetch fails/times out or returns `status:"failed"`,
     leave the kind labels in place. Names are cosmetic and never affect the estimate, so
     snapshot/live drift is harmless (D6). No error UI required (optional subtle note).
   - Keep markers from the snapshot authoritative for **position/count**; never add/remove
     markers based on the live fetch (that would make the map inconsistent with the counts
     that produced the estimate). Only patch tooltip text.

### 5.8 `fetch_amenities` TTL cache (D9)

`src/stimmo/data/amenities.py:176`. Wrap with a process-local TTL+LRU cache keyed by
**quantized** coordinates so nearby opens share a hit.

```python
# new cache interface (mirror InMemoryCache style; injectable clock for tests)
class _AmenityCache:
    def __init__(self, *, ttl_s: float = 3600, maxsize: int = 512, clock=time.monotonic): ...
    def get(self, key) -> AmenityScore | None: ...
    def put(self, key, value: AmenityScore) -> None: ...

def fetch_amenities(lat: float, lon: float, *, _cache=_default_cache) -> AmenityScore:
    key = (round(lat, 3), round(lon, 3))   # ~111 m grid -> coalesces near-identical opens
    hit = _cache.get(key)
    if hit is not None: return hit
    score = _fetch_amenities_uncached(lat, lon)   # current body
    _cache.put(key, score)
    return score
```

- Quantization grid: `round(_, 3)` ≈ 111 m. Acceptable: amenity counts within ~100 m are
  effectively identical, and this is only used for the *name* enrichment + the live form
  flow (the estimate on shared views is recomputed from the stored snapshot, not this
  call). Confirm grid at implementation; 3dp is the default.
- TTL default 1 h; LRU cap 512 entries. Same swap-to-Redis seam as the MCP cache if a
  second replica is ever added.
- Tests: injectable clock; assert second call within TTL does not hit `_query`.

### 5.9 Metrics additions

- Add an outcome path for **store hit vs legacy-fallback** so we can watch adoption /
  decide GC and eventual legacy-decoder removal. Either:
  - new label value on the existing counter, or
  - a small `stimmo_share_resolve_total{path="store"|"legacy"|"miss"}` counter.
- Keep existing `stimmo_share_events_total{event,outcome}` semantics (open/og_render ×
  ok/invalid/error).
- Optional: `stimmo_amenity_cache_total{result="hit"|"miss"}`.
- Respect the dispatcher-level instrumentation invariant (CLAUDE.md Observability): these
  are domain counters incremented in handlers, not in `_dispatch`.

### 5.10 Compose / ops changes (D8 — co-deploy with write path)

`deploy/docker-compose.yml`, `stimmo` service. Note existing convention: prometheus/grafana
use **host bind mounts under `/opt/stimmo/`**, not named volumes — follow suit.

```yaml
  stimmo:
    image: ghcr.io/alediaferia/stimmo:latest
    environment:
      - STIMMO_METRICS_PORT=9100
      - STIMMO_SHARE_DB=/data/share.db        # NEW
    volumes:
      - /opt/stimmo/share-data:/data          # NEW (host bind mount, like prom/grafana)
```

- Create `/opt/stimmo/share-data` on the VPS (writable by the container user) before
  deploy.
- **Ordering is critical:** the image carrying the write-path change and this compose edit
  **must land together**. If the app starts writing `/s/<id>` links while the volume is
  absent, the DB is in the ephemeral layer and every `docker compose pull` wipes it →
  freshly-shared links 404 after the next deploy.
- **Backup hook:** document a simple cron `sqlite3 /opt/stimmo/share-data/share.db
  ".backup /opt/stimmo/backups/share-$(date +%F).db"` (or copy the file — WAL-safe via
  `.backup`). Not required for v1 launch but note it; durability of new links depends on it.
- WAL produces `-wal`/`-shm` sidecar files in `/data`; that's expected, no action needed.

---

## 6. Sequenced step plan

Each step is independently shippable and leaves the tree green. Effort: S ≤ ~1h focused,
M ~ half-day, L ~ 1–2 days.

| Step | What | Owner | Effort | Depends on |
|------|------|-------|--------|------------|
| **0** | Refactor `share.py` into payload/blob primitives (5.3) without changing `encode`/`decode` behaviour; add tests proving legacy round-trip unchanged. | stimmo-maintainer | S | — |
| **1** | Add `"m"` marker list (kind+coord, no names) to the **stored blob** path (`encode_blob`/`decode_blob`), capped at 12. Unit tests. | stimmo-maintainer | S | 0 |
| **2** | New `src/stimmo/web/share_store.py` (`SqliteShareStore`, schema 5.1, content-addressed id 5.2, collision read-back). Unit tests with tmp_path + in-memory + injected clock. | stimmo-maintainer | M | 1 |
| **3** | Wire store into `app.py`: construct from `STIMMO_SHARE_DB`; write path emits `/s/<id>` + `/og/<id>.png`; add `GET /s/{id}` (lang-free, negotiate locale); make OG + legacy resolve via `resolve` (D5). Retain legacy `/{lang}/s/{token}`. Metrics (5.9). Tests incl. dual-path + locale negotiation + legacy-link still resolves. | stimmo-maintainer | M | 2 |
| **4** | Fix bug #2: ensure shared views populate `items_within_500m` from stored markers so the map+markers render (result.html:426). Tests/snapshot. | stimmo-maintainer | S | 3 |
| **5** | Async name enrichment client flow (5.7): IntersectionObserver → `/api/amenities` → nearest-match tooltip patch, graceful degradation. | stimmo-maintainer | M | 4 |
| **6** | `fetch_amenities` TTL+LRU cache (5.8) with injectable clock + tests. | stimmo-maintainer | S | — (parallel) |
| **7** | Compose: `/data` volume + `STIMMO_SHARE_DB` on `stimmo` (5.10); create host dir; document backup. **Co-deploy with step 3's image (D8).** | github-ci-pipeline-maintainer | S | 3 |
| **8** | End-to-end validation against a live Milan listing: import → estimate → share → open short link (markers render, names enrich, locale negotiates) → OG image; and confirm a **pre-existing long link still resolves**. | stimmo-e2e-validator | S | 4,5,7 |
| **9** | *(Optional, deferrable — D10)* Binary/schema-aware codec to shrink the retained legacy fallback token only. | stimmo-maintainer | M | 0 |

**Critical co-deploy:** steps 3 and 7 must reach production together. Land the app change
on `main` (CI builds the image), then `github-ci-pipeline-maintainer` updates compose +
creates the host dir, then `docker compose pull && up -d`. Validate (step 8) immediately
after.

---

## 7. Open questions / decisions needed before or at implementation

1. **GC retention window `N` and GC mechanism (D7).** Columns added now; the actual policy
   (e.g. delete rows with `last_seen` older than 12–24 months, run via cron or an app
   startup sweep) is deferred. **Decision needed before the table grows large**, not before
   launch. Watch `stimmo_share_resolve_total` / row count to inform it.
2. **Exact id length `N` (5.2).** Default 8; confirm at implementation. Revisit only if a
   collision is ever observed (read-back guard makes this safe regardless).
3. **`STIMMO_SHARE_DB` default when env unset** (local dev/tests): `:memory:` vs repo-local
   `var/share.db` vs tmp. Pick one; tests should inject their own store via the
   constructor regardless.
4. **`last_seen` bump throttling (5.1).** Simple (bump every read) vs throttled (bump if
   stale > 1 h). Default simple; revisit only under write pressure.
5. **Whether legacy `encode` should also emit `"m"` markers** (5.3). Default no (keeps the
   fallback token small; new links go through the store anyway).
6. **Should the legacy `/{lang}/s/{token}` route eventually be removed?** Not now (D5,
   indefinite). Revisit only if `stimmo_share_resolve_total{path="legacy"}` drops to ~zero
   over a long window.

---

## 8. Acceptance criteria

**Per-step (summarised; each step's tests live with the code):**

- **Step 0:** legacy `encode`/`decode` round-trip is byte-identical to before for a fixed
  fixture; new primitives produce the same blob the legacy token wraps.
- **Step 1:** a property with N≤12 amenity items round-trips `kind`+coords through
  `encode_blob`/`decode_blob`; names are absent; >12 items are capped to 12.
- **Step 2:** `put(blob)` is idempotent (same blob → same id, one row); `get` returns the
  blob and bumps `last_seen`; unknown id → `None`; the synthetic prefix-collision case
  (two different blobs, forced same prefix) is handled without data loss.
- **Step 3:** posting an estimate yields a `share_url` of the form `…/s/<≤10 char id>` and
  `…/og/<id>.png` (no lang prefix); `GET /s/<id>` renders the result; locale is negotiated
  cookie→Accept-Language→it_IT; **a stored pre-existing legacy long token still resolves**
  via `resolve`; metrics distinguish store vs legacy resolves.
- **Step 4:** a shared short-link view renders the map **with** amenity markers (property
  dot + kind-coloured dots); tooltips show the kind label initially.
- **Step 5:** with `/api/amenities` reachable, tooltips upgrade to real names within a
  couple of seconds after the map scrolls into view; with it unreachable/timed-out, kind
  labels persist and no error is thrown; marker count/positions never change from the
  fetch.
- **Step 6:** two `fetch_amenities` calls for coordinates within the same quantization cell
  inside the TTL produce exactly one `_query` Overpass call (verified via injected clock +
  spy).
- **Step 7:** after deploy, `STIMMO_SHARE_DB` resolves to `/data/share.db` on the mounted
  volume; a link created pre-`docker compose pull` still resolves post-pull (durability).
- **Step 8 (e2e):** real Milan listing → import → estimate → short link opens with markers
  + names + correct locale; OG image renders; **a known old long link still resolves**.

**Global invariant checks (must all hold):**

- No multiplier/tuning logic added outside `valuation/adjustments.py`.
- Shared-view estimate numbers are recomputed by `engine.estimate` from bundled data (store
  holds only snapshot inputs, not computed prices).
- Single-process topology unchanged; SQLite access behind the `ShareStore` interface.
- No new runtime fetch of *reference* data; only the existing Nominatim/Overpass live calls.
- All existing tests still pass; CI green; the OG/crawler path never 5xx.
