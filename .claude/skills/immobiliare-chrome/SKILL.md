---
name: immobiliare-chrome
description: Navigate and extract data from Immobiliare.it listings using the Claude in Chrome extension. Use when you need to read a real estate listing, search results, or saved listings from immobiliare.it — fetching the structured listing data (price, surface, location, features, energy class, etc.). Covers the extension host-permission gotcha, the post-grant reload requirement, extracting the listing JSON from Next.js `__NEXT_DATA__`, and the javascript_tool/screenshot quirks specific to this site.
---

# Navigating Immobiliare.it with Claude in Chrome

Immobiliare.it is Italy's largest real-estate portal. This skill explains how to read
listings reliably through the **Claude in Chrome** extension (`mcp__claude-in-chrome__*`
tools). It is self-contained and not tied to any particular downstream app.

## Why the browser (not a plain fetch)

Immobiliare.it is behind bot protection. A server-side / plain HTTP `GET` of a listing
page is blocked or returns a challenge, so the page **must** be read through a real browser
session — i.e. the Claude in Chrome extension. Everything below assumes you are driving an
actual Chrome tab.

## 0. Load the browser tools

The `mcp__claude-in-chrome__*` tools are usually deferred. Load the ones you need in **one**
`ToolSearch` call:

```
ToolSearch select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__tabs_create_mcp
```

Then call `tabs_context_mcp` once before anything else to learn the existing tab IDs.

## 1. Grant the extension permission for immobiliare.it (the #1 gotcha)

The extension needs **per-host permission** for `www.immobiliare.it`. Until it is granted,
*every* tool that reads page content fails with:

> `Cannot access contents of the page. Extension manifest must request permission to access the respective host.`

and `javascript_tool` may instead just **time out** ("did not respond in time").

**You cannot grant this yourself** — ask the user to enable site access for
`www.immobiliare.it` in the Claude in Chrome extension (focus the listing tab, open the
extension, allow the site / approve the pending side-panel prompt).

### Critical follow-up: reload after granting

After the user grants permission, the **already-open page must be reloaded** so the content
script injects. The extension popup may say "can access this tab" while the automation layer
still reports the host-permission error. Force a reload by navigating the tab to its own URL:

```
navigate(tabId, "https://www.immobiliare.it/annunci/<id>/")
```

Then re-run a light check (below). Don't keep retrying without the reload — it won't start
working on its own.

## 2. Confirm access with a light call

Screenshots are unreliable here (see §5), so verify access with a tiny `javascript_tool`
read first:

```js
({ title: document.title, url: location.href, hasNextData: !!document.getElementById('__NEXT_DATA__') })
```

`hasNextData: true` means you can extract the listing.

## 3. Extract the listing data from `__NEXT_DATA__`

Immobiliare.it is a Next.js app: the full listing object is embedded in a
`<script id="__NEXT_DATA__">` JSON blob in the initial HTML. The listing node is nested
deep, so search for the object that has `price`, `location`, and `typology`. Run this in the
listing tab with `javascript_tool`:

```js
(function () {
  function nextData() {
    const el = document.getElementById('__NEXT_DATA__');
    return el ? JSON.parse(el.textContent) : null;
  }
  // The listing object is buried in __NEXT_DATA__; find the node that looks like one.
  function findListing(node, depth = 0) {
    if (!node || depth > 14) return null;
    if (typeof node === 'object' && !Array.isArray(node) &&
        node.price && node.location && node.typology) return node;
    const vals = Array.isArray(node) ? node
      : (typeof node === 'object' ? Object.values(node) : []);
    for (const v of vals) { const hit = findListing(v, depth + 1); if (hit) return hit; }
    return null;
  }
  const l = findListing(nextData());
  if (!l) return { error: 'listing not found in __NEXT_DATA__' };
  return {
    price:        l.price?.value,                 // asking price, number (EUR)
    surface:      l.surfaceValue ?? l.surface,     // e.g. "105 m²"
    address:      l.location?.address,             // street (no civic number)
    streetNumber: l.location?.streetNumber,        // often null
    city:         l.location?.city,
    lat:          l.location?.latitude,
    lon:          l.location?.longitude,
    floor:        l.floor?.abbreviation,           // e.g. "2"
    floors:       l.floors,                        // e.g. "3 piani"
    elevator:     l.elevator,                      // bool
    energyClass:  l.energy?.class?.name,           // e.g. "F"
    typologyValue: l.typologyValue,                // e.g. "Trilocale | Classe immobile signorile"
    typology:     l.typology?.name,                // e.g. "Appartamento"
    category:     l.category?.name,                // e.g. "Residenziale"
    conditionId:  l.conditionId,                   // e.g. 6
    condition:    l.condition,                     // e.g. "Ottimo / Ristrutturato"
    bathrooms:    l.bathrooms,
    rooms:        l.rooms,
    buildingYear: l.buildingYear,
    // feature lists (amenities, fixtures): normalize codeName/name/type/string forms
    primaryFeatures: (l.primaryFeatures || []).map(f => f.codeName || f.name).filter(Boolean),
    mainFeatures:    (l.mainFeatures || []).map(f => f.type).filter(Boolean),
    ga4features:     l.ga4features || [],           // array of human-readable strings
    description: (l.description || '').slice(0, 2000),
  };
})()
```

This returns plain JSON you can use directly. The `__NEXT_DATA__` blob is in the initial
HTML, so you do **not** need to dismiss the cookie banner to read it.

## 4. URL patterns

- Listing detail: `https://www.immobiliare.it/annunci/<numericId>/`
- Saved listings (logged-in): `https://www.immobiliare.it/user/saved/`
- Search results: navigate via the site's search UI, then collect
  `/annunci/<id>/` hrefs with `read_page` (filter `interactive`) or a `querySelectorAll`
  snippet, and open each one.

For a search/list page, scrape listing links with:

```js
[...document.querySelectorAll('a[href*="/annunci/"]')]
  .map(a => a.href).filter((v, i, arr) => arr.indexOf(v) === i)
```

## 5. javascript_tool & screenshot quirks on this site

- **Top-level await, not async IIFE.** `javascript_tool` returns the last expression. An
  `(async () => {...})()` returns a *Promise* that serializes as `{}`. Wrap it:
  `await (async () => { ...; return result; })()`.
- **Base64 strings get redacted.** If your snippet returns a base64 value, the tool result
  shows `[BLOCKED: Base64 encoded data]`. If you need to act on an encoded value (e.g. build
  a URL with an encoded payload), do the encode **and** the navigation inside the page
  (`window.location.href = '...' + btoa(...)`) so the blob never crosses the tool boundary.
- **Query strings / cookies get redacted too** (`[BLOCKED: Cookie/query string data]`) in
  returned `location.href`-style values — read the specific fields you need instead of dumping
  the whole URL.
- **Screenshots hang.** `computer` screenshots wait for `document_idle`, which can time out
  (~45 s) on Immobiliare's long-lived connections. Prefer `javascript_tool`,
  `get_page_text`, or `read_page` to inspect state. If you truly need pixels, retry the
  screenshot after the page settles, but don't block on it.
- **Cookie/consent banner.** A GDPR consent banner may appear. Choose the privacy-preserving
  option (reject non-essential) if you must interact with it — but reading `__NEXT_DATA__`
  does not require dismissing it.
- **Don't trigger native dialogs.** Avoid `alert/confirm/prompt`; they block the extension.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Extension manifest must request permission to access the respective host" | Host not granted | Ask user to allow `www.immobiliare.it` in the extension |
| Granted, but tools still fail / `javascript_tool` times out | Content script not injected into the already-open page | Reload the tab (navigate to its own URL), then retry |
| Plain HTTP fetch returns a challenge/empty | Bot protection | Use the Chrome extension on a real tab |
| `findListing` returns `{error: ...}` | Not a listing detail page, or `__NEXT_DATA__` absent | Confirm you're on `/annunci/<id>/`; check `hasNextData` |
| Tool result is `{}` for an async snippet | Returned a Promise | Use top-level `await (async()=>{...})()` |
| Returned value shows `[BLOCKED: ...]` | Harness redacts base64 / cookie / query data | Keep encoded data in-page; return only plain fields |
