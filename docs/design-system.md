# Design system

The visual identity is "civic, transparent, document-like": paper-and-ink palette, hairline-bordered cards, three deliberately chosen typefaces, and a terracotta accent that reads as a civic/institutional red-brown.

## Where CSS lives

**All CSS lives in [`web/templates/base.html`](../src/stimmo/web/templates/base.html)** inside a single `<style>` block. There is no external `.css` file and no CSS framework. This is intentional:

- The entire UI is server-rendered Jinja2. There is no build step, no bundler, no tree-shaking concern.
- A single location makes the design system auditable in one read.
- If you add new component styles, add them to `base.html`'s `<style>` block in the section that logically belongs (see the section comments).

Do not add an external stylesheet or a `<link>` to a CDN stylesheet other than the existing Google Fonts and Leaflet imports.

## Typography

Three families, each with a specific role:

| Variable | Family | Role |
|---|---|---|
| `var(--serif)` | Instrument Serif | Wordmark, headings, verdict label, large numerics on result page, `<em>` accents |
| `var(--sans)` | Inter Tight | Body copy, labels, buttons, UI chrome |
| `var(--mono)` | JetBrains Mono | All monetary values, percentages, zone codes, semester labels, eyebrows, keyboard shortcuts |

The rule: **numbers are always mono**. Anything that is a data value (€/m², percentage, semester string) gets `font-family: var(--mono); font-variant-numeric: tabular-nums`. Use the `.mono` utility class or `dl.kv dd.mono` in definition lists.

## Colour tokens

Colors use OKLCH throughout — this gives perceptually uniform hue shifts and predictable lightness across the accent/verdict variants.

```
--paper           background (warm off-white)
--paper-2         slightly darker paper (card interiors, input bg)
--paper-rule      hairline borders (most borders)
--paper-rule-2    stronger rule (input borders, segmented control)

--ink             primary text
--ink-2           secondary text (body paragraphs inside cards)
--ink-muted       tertiary (labels, eyebrows, captions)
--ink-fade        quaternary (hints, disabled, axis labels)

--accent          terracotta (single primary accent)
--accent-soft     pale terracotta (focus rings, hover states)

--good            green — "under-priced" verdict
--warn            amber — "fair" verdict
--bad             red — "over-priced" verdict + error states
```

Each verdict colour has a `-soft` variant (e.g. `--good-soft`) for background fills. The `data-v` attribute on `.verdict-hero` sets `--vh` to the appropriate verdict colour; child elements (`.dot`, `.verdict-label em`, `.marker .stem`, `.marker .flag`) all read `var(--vh)` so they change automatically with the verdict.

## Component classes

### `.doc`
The primary container. A hairline-bordered card with `border-radius: 2px` (sharp, document-like — not the rounded-rectangle SaaS look). Use `.doc-head` with `.doc-title` (Instrument Serif, 22 px) and `.doc-eyebrow` (mono, 10 px, uppercase) for the card header.

### `.note`
A left-bordered callout, accent-coloured left bar by default. Use `.note.error` for error conditions (red bar). Do not use for primary content — only for warnings, caveats, and context.

### `.btn` / `.btn-ghost`
Primary action: dark fill, paper text. Ghost variant: transparent with hairline border. Both use `display: inline-flex` so they can contain the serif italic arrow `<span class="arrow">→</span>`.

### `.seg`
Segmented control for tight enum choices. Used for the OMI condition picker on the form (ottimo / normale / scadente). Requires a `<input type="hidden">` wired by a small `setOmiCond()` vanilla-JS handler — see `form.html`.

### `.pagehead`
Two-column grid (`1fr auto`): left side holds the stepper + `<h1>` + `.sub` paragraph; right side holds a `.meta` mono block (zone, semester, counts). On mobile (`≤ 880px`) collapses to single column.

### `.grid-2`
The main two-column content grid (`1.1fr 1fr`, aligns to top). Used on form (left = form docs, right = map + methodology), result (left = adjustments/history, right = numbers/amenities), bookmarklet (left = drag + steps + fallback, right = browser mock). Collapses to single column at 880 px.

### `.strip`
8-column histogram for OMI semester history and NTN quarterly data. Column heights are CSS `height: <n>%` set inline by the template from Python-computed values. The current/last column gets class `.cur` for the terracotta highlight.

### `.gauge`
The band gauge on the result page. Column heights and horizontal positions are computed in `app.py` (see [architecture.md](architecture.md#band-gauge-geometry)) and injected as inline `style="left: N%; width: N%"`. Do not attempt to compute gauge geometry in Jinja — the maths involves a dynamic viewport range that Jinja's filter set can't cleanly express.

## Purchase-cost components

Added to the same `base.html` `<style>` block (section "Purchase costs"):

- `.costs-summary` — auto-fit grid of `.vstat` figures (closing costs, renovation, total, cash needed) with a mono `.rng` range line under each value; `.vstat.total` takes the accent colour.
- `.tag` — small mono status chip beside a figure: plain for `statutory` / `typical range`, `.indicative` (amber) for ranges not confirmed current, `.needs_input` (red) when an input is missing. Anything indicative must carry it; do not show an indicative range as a firm number.
- `details.costs-lines` / `details.costs-adjust` — native, JavaScript-free disclosure for the line table and the assumptions form.
- `.ltv` — the mortgage % slider (a native `input[type=range]`, progressive enhancement, `hidden` until `static/costs-form.js` runs). `--ltv-frac` (limit / ceiling, from `costs/rates.py`) places the `.ltv-mark` and starts the `--warn-soft` caution zone on the track; `data-zone="caution"` turns the thumb `--warn`; `.ltv-caution` is the amber note shown above the ordinary limit. `.ltv-equiv` is the live `= €…` / `≈ …%` line and `.ltv-error` the client-side validation message (`--bad`).
- `details.doc.costs-details` — the calculator page's methodology: a `<details class="doc">` whose `<summary class="doc-head">` is the section head (a `+` / `−` after the title, no marker), collapsed by default, no script. `#not-included` is a separate, always-open `.doc`.
- `.rate-ref` — the tinted box under the mortgage-rate field listing Banca d'Italia's market averages (`.rate-ref-head`, `.rate-ref-list`); a past-review reference carries `.tag.stale.ref-stale`. `.costs-excluded` is the one-line "not included" under the totals; `.form-alt` is the home page's line into the calculator.
- `.doc.interest` — dashed, tinted card for a feature that is not available yet; it always carries a `.tag.indicative` saying so.

## Paper texture

`body::before` injects a faint `repeating-linear-gradient` horizontal hairline pattern every 32 px. This sits at `z-index: 0`; `.app` is at `z-index: 1` to stay above it. If you need to remove the texture for a specific context, set `--tex: 0` on a parent element (not currently wired to any toggle in production).


## Mobile breakpoint

`max-width: 880px` collapses multi-column layouts (`.grid-2`, `.brand-row`, `.pipeline`, `.src-grid`, `.not-grid`, `.explainer`) to single column and scales down headline type (`h1` from 56 px → 40 px, `.verdict-label` from 64 px → 44 px, `.wm-big` from 96 px → 64 px). The breakpoint is set once at the bottom of each relevant rule — do not add per-component JS-based resize logic.

## BrandStrip placement

The BrandStrip (wordmark showcase card + dark numerics card) appears **only on `/about`**. It is brand documentation, not a persistent banner. The form, result, bookmarklet, and error pages use a clean topbar instead.
