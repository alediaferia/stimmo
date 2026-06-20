# Sharing estimates

stimmo turns any estimate into a **self-contained shareable link** that re-renders the full result page and unfurls as a rich social card. The goal is to let people refer back to a stimmo estimate with a URL instead of a screenshot.

Two routes back the feature:

| Route | Purpose |
|---|---|
| `GET /{lang}/s/{token}` | Decode a token and render the full result page ([result.html](../src/stimmo/web/templates/result.html)) without hitting live services. |
| `GET /og/{token}.png` | Render the 1200×630 Open Graph social card (PNG) for the same token. |

A **Share ↗** button on the result page copies the canonical link to the clipboard. The link's absolute URL and `og:image` are derived from `request.base_url`, so they work behind the Cloudflare tunnel.

There is **no datastore** — the URL *is* the storage. This keeps the feature consistent with stimmo's stateless single-VPS deployment.

## The token

Encoding lives in [web/share.py](../src/stimmo/web/share.py):

```
b'\x01' + zlib.compress(compact_json)  →  base64url (no padding)
```

The JSON payload carries only the inputs needed to skip the slow live services and reproduce everything else deterministically:

- every `Property` field (the listing facts the user entered),
- the **geocode result** `lat`/`lon` (the expensive Nominatim call),
- the **`AmenityScore`** (the expensive Overpass call), minus `items_within_500m` (the verbose map markers — a shared view renders without the pins).

A first version byte (`0x01`) prefixes the payload for forward-compatibility. `decode()` raises `ShareTokenError` on anything malformed, truncated, version-mismatched, or larger than a 64 KB decompressed sanity guard (a zip-bomb guard).

Typical encoded length is **~420–480 base64url chars**; a fully-loaded property URL stays comfortably under ~570 chars — well within social-crawler and browser limits.

### Why we encode inputs, not the estimate

The token deliberately stores **inputs, not outputs**. On open, `share_view` / `og_image` recompute the zone lookup → `omi.lookup` → `engine.estimate` (plus history and NTN panels) from the *currently bundled* OMI data and the *current* `valuation/adjustments.py` coefficients. The verdict, €/m², multiplier, and gauge are never frozen.

The consequences are intentional:

- **Fast opens.** The two genuinely slow dependencies — Nominatim and Overpass — are cached in the token, so opening a link never blocks on them. Everything recomputed is local, bundled, and cheap.
- **Links self-update.** Refresh OMI or retune coefficients and every old link reflects the new estimate automatically — there is no "stale frozen number" to regenerate.
- **Possible divergence.** The flip side: a number (or even the verdict) can change between when a link was shared and when it is later opened. To keep that legible, the OG card stamps the **OMI semester** the estimate was computed against (e.g. `OMI 2025 H2`), and the `stimmo_share_events_total{outcome="error"}` metric surfaces links that no longer recompute (see [Observability](#observability)).

What stays frozen is the `AmenityScore`: a new metro stop near the address won't be reflected in an old link. That's an accepted trade-off for fast, stateless opens.

## The social card

[web/ogimage.py](../src/stimmo/web/ogimage.py) renders the OG card with **Pillow** — pure Python, no headless browser. It mirrors the on-page verdict card:

- verdict eyebrow + the colour-coded verdict sentence (under/fair/over),
- address · surface,
- the OMI/typical-ask gauge with the asking-price marker,
- the four verdict stats: **Δ vs typical ask mid**, **adjusted €/m²**, **asking €/m²**, **multiplier on OMI band**,
- the `stimmo` wordmark and an `OMI {semester} · no ML` revision stamp.

The card is **locale-neutral** (en-style numerals) and served with `Cache-Control: public, max-age=86400, immutable` — the token is deterministic, so the image is safely cacheable.

### Fonts

The card bundles the **full-charset Inter Tight variable TTF** ([static/fonts/inter-tight-var.ttf](../src/stimmo/web/static/fonts/inter-tight-var.ttf), OFL, from Google Fonts) and sets the weight via the `wght` variation axis.

> ⚠️ Do **not** point the card renderer at the site's `inter-tight-*.woff2` webfonts. Those are per-page **subsets** — their cmap covers only the glyphs used on stimmo's own pages (basically just `A` from basic Latin), so an arbitrary address or price renders as `.notdef` tofu. `tests/test_share.py::TestOgImageFont` guards this by asserting the bundled font covers the required glyph set.

### Meta tags

[result.html](../src/stimmo/web/templates/result.html) overrides the base `{% block og_meta %}` to emit per-estimate `og:title` / `og:description` / `og:image` / `og:url` and `twitter:card=summary_large_image`. Because stimmo is server-rendered, the tags are present for crawlers (WhatsApp, Slack, Twitterbot, Facebook) on first fetch.

## Observability

`stimmo_share_events_total` (Counter, in [web/metrics.py](../src/stimmo/web/metrics.py)) tracks adoption and token health:

| Label | Values |
|---|---|
| `event` | `open` (share page) · `og_render` (card image) |
| `outcome` | `ok` · `invalid` (bad/forged/expired token) · `error` (valid token, recompute failed) |

It is incremented **in the `share_view` / `og_image` handlers**, not the dispatcher middleware, because invalid/expired tokens are served as a normal 400/404 and would otherwise be indistinguishable from other failures in `stimmo_http_requests_total`. Read it as:

- **Adoption** — `outcome="ok"` opens (referrals back) and OG renders (unfurls).
- **Abuse / corruption** — `invalid` rate flags forged or broken links.
- **Data divergence** — `error` rate flags old links that no longer recompute against refreshed data.

## Local development

`uv run stimmo-web` serves the routes at `http://127.0.0.1:8000`. To mint and inspect a token without the browser:

```python
from stimmo.web import share

token = share.encode(prop, lat, lon, amenity)   # prop: Property, amenity: AmenityScore
print(f"http://127.0.0.1:8000/it/s/{token}")
print(f"http://127.0.0.1:8000/og/{token}.png")

prop2, lat2, lon2, amen2 = share.decode(token)   # round-trips; raises ShareTokenError if bad
```

To preview the card image directly, call `ogimage.render(est, prop)` with an `Estimate` from `engine.estimate(...)` and write the returned bytes to a `.png`.

## Invariants

- **No pricing logic in the share/OG paths.** Both routes go through the existing `engine.estimate`; `valuation/adjustments.py` stays the only tuning surface.
- **`/og/{token}.png` is lang-agnostic** and uses a named endpoint function so the Prometheus `route` label stays clean.
- **Schema changes need a version bump.** If the encoded `Property`/`AmenityScore` shape changes, bump the token version and keep an old-version decoder if existing links must keep resolving.
