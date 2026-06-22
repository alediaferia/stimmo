# immobiliare-chrome

A standalone [Claude Code](https://claude.com/claude-code) **skill** for reading
Immobiliare.it real-estate listings through the **Claude in Chrome** browser extension.

It teaches Claude how to: get past the site's bot protection (by using a real browser),
clear the extension's per-host permission gotcha, and pull the structured listing data
(price, surface, location/coords, floor, energy class, condition, features, description, …)
straight out of the page's Next.js `__NEXT_DATA__` blob.

The skill is self-contained — it does not depend on any particular downstream app.

## Prerequisites: Claude in Chrome

**Claude in Chrome** is Anthropic's browser extension that lets Claude view and control tabs
in your Chrome session. The skill drives it via the `mcp__claude-in-chrome__*` tools.

### Install the extension

1. Use Google Chrome (or a Chromium-based browser).
2. Install **Claude in Chrome** from Anthropic's official distribution. Availability is
   gated and the distribution channel can change, so follow the current official setup
   guide rather than a hard-coded link: <https://www.anthropic.com/claude/chrome> (and the
   Claude in Chrome help docs at <https://support.anthropic.com>).
3. Sign in with your Claude account when prompted, and pin the extension.
4. **Grant site access for `www.immobiliare.it`.** This is mandatory — without it every
   page-reading tool fails with *"Extension manifest must request permission to access the
   respective host."* Open the listing tab, open the extension, and allow the site (or
   approve the pending side-panel prompt). After granting, **reload the tab** so the content
   script injects (see the skill's §1).

> Security note: the extension can act on pages you grant it. Only enable it on sites you
> trust, and review actions before confirming anything irreversible.

## Install the skill

Skills are auto-discovered from `SKILL.md` files in a `skills/` directory.

- **Project scope** (shared with a repo): copy the `immobiliare-chrome/` folder into the
  project's `.claude/skills/` directory.
- **Personal / global scope**: copy it into `~/.claude/skills/`.

```
.claude/skills/
└── immobiliare-chrome/
    ├── SKILL.md   # the skill instructions Claude loads
    └── README.md  # this file
```

Claude Code picks it up automatically; no restart needed for new sessions.

## Use it

With the extension installed and immobiliare.it permitted, just ask in natural language —
Claude will invoke the skill. Examples:

- "Open this Immobiliare listing and pull out the price, size, and location: `<url>`."
- "From immobiliare.it, read my saved listings and list each one's price and surface."

You can also invoke it explicitly with `/immobiliare-chrome`.

## Sharing

This skill is intentionally free of project-specific logic, so it's safe to share. Copy the
`immobiliare-chrome/` directory into any project's `.claude/skills/` (or `~/.claude/skills/`)
and it works as-is.

## Files

- **`SKILL.md`** — the instructions Claude loads: permissions flow, `__NEXT_DATA__`
  extraction snippet + field reference, URL patterns, `javascript_tool`/screenshot quirks,
  and a troubleshooting table.
