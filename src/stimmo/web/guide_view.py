"""Web glue for the editorial guides: Markdown body -> safe HTML.

Everything here is presentation. The files, their front-matter and their rules are in
`data/guides.py`; the route, the SEO registration and the JSON-LD are in `web/app.py`.

The Markdown comes from a private repository, so it is treated as untrusted anyway:

* raw HTML is never rendered (it is escaped as text), and an image is reduced to its alt text,
  so a guide can never make the reader's browser fetch from, or run anything of, another host;
* a link survives only if it is an in-page `#anchor`, an `/it/…` path or an `https://` URL.
  Anything else (`http:`, `javascript:`, `data:`, a relative path) keeps its text and loses the
  `<a>`. So does a link to `/it/guide/{slug}` whose guide does not exist, which is what makes
  publishing order-independent: a guide can link to one that is not published yet, and the
  link appears by itself when that file lands.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.renderer import RendererHTML
from markdown_it.rules_core import StateCore
from markupsafe import Markup

from stimmo.data import guides
from stimmo.data.guides import Guide

GUIDE_PATH = re.compile(r"/it/guide/([a-z0-9]+(?:-[a-z0-9]+)*)")


def link_is_kept(href: str, guide_slugs: frozenset[str]) -> bool:
    """Whether a link keeps its `<a>`. See the module docstring for the rule."""
    if href.startswith("#") or href.startswith("https://"):
        return True
    if not href.startswith("/it/") or "\\" in href:
        return False
    path = re.split(r"[?#]", href, maxsplit=1)[0]
    if {".", ".."} & set(path.split("/")):
        return False
    if path == "/it/guide" or path.startswith("/it/guide/"):
        match = GUIDE_PATH.fullmatch(path)
        return match is not None and match.group(1) in guide_slugs
    return True


class _GuideRenderer(RendererHTML):
    # markdown-it-py registers the public methods of the renderer as its token rules.

    def link_open(self, tokens, idx, options, env) -> str:
        kept = link_is_kept(tokens[idx].attrGet("href") or "", env["guide_slugs"])
        # Links do not nest in CommonMark, but a stack keeps open and close paired regardless.
        env["link_kept"].append(kept)
        return self.renderToken(tokens, idx, options, env) if kept else ""

    def link_close(self, tokens, idx, options, env) -> str:
        return self.renderToken(tokens, idx, options, env) if env["link_kept"].pop() else ""

    def image(self, tokens, idx, options, env) -> str:
        # Never an <img>: only the alt text stays, so no guide can make a browser fetch from
        # another host.
        return escapeHtml(tokens[idx].content)

    # Tables scroll inside their own box instead of widening the page (.table-scroll is the
    # wrapper the cost tables use; its rule is in base.html).
    def table_open(self, tokens, idx, options, env) -> str:
        return '<div class="table-scroll">' + self.renderToken(tokens, idx, options, env)

    def table_close(self, tokens, idx, options, env) -> str:
        return self.renderToken(tokens, idx, options, env) + "</div>\n"


def slugify(text: str) -> str:
    """ASCII, lower-case, hyphenated: "L'esempio, voce per voce" -> "l-esempio-voce-per-voce"."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-") or "section"


def _heading_text(inline_token) -> str:
    parts: list[str] = []
    for child in inline_token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


def _heading_ids(state: StateCore) -> None:
    """Give every h2 and h3 an `id` derived from its text, so `#anchor` links work. Repeats get
    a numeric suffix, so ids stay unique and stable for a given text."""
    seen: dict[str, int] = {}
    for index, token in enumerate(state.tokens):
        if token.type != "heading_open" or token.tag not in ("h2", "h3"):
            continue
        base = slugify(_heading_text(state.tokens[index + 1]))
        count = seen.get(base, 0) + 1
        seen[base] = count
        token.attrSet("id", base if count == 1 else f"{base}-{count}")


def _build_parser() -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": False, "typographer": False},
        renderer_cls=_GuideRenderer,
    ).enable("table")
    # markdown-it refuses javascript:/data: links outright and leaves the whole `[x](…)` source
    # in the text. Accept every destination here and let link_is_kept decide, so that the text
    # alone survives, as for every other dropped link.
    md.validateLink = lambda _url: True
    md.core.ruler.push("guide_heading_ids", _heading_ids)
    return md


_MD = _build_parser()


def _env(guide_slugs: frozenset[str]) -> dict:
    return {"guide_slugs": guide_slugs, "link_kept": []}


@lru_cache(maxsize=128)
def _render(guide: Guide, guide_slugs: frozenset[str]) -> Markup:
    return Markup(_MD.render(guide.body_md, _env(guide_slugs)))


def render_body(guide: Guide) -> Markup:
    """The guide's body as safe HTML, rendered once per process.

    The cache is keyed on the guide itself and on the set of existing guides (which decides the
    link gating), so it can never serve a page that disagrees with what the loader holds.
    """
    return _render(guide, frozenset(g.slug for g in guides.list_guides()))


# The same author the calculator page names as its creator (templates/costs.html).
AUTHOR: dict[str, str] = {
    "@type": "Person",
    "name": "Alessandro Diaferia",
    "url": "https://github.com/alediaferia",
}


# schema.org's own IRI for the IPTC "composite with trained algorithmic media" digital source.
AI_ASSISTED_SOURCE_TYPE = "https://schema.org/CompositeWithTrainedAlgorithmicMediaDigitalSource"


def article_json_ld(guide: Guide, canonical_url: str, organization_id: str) -> dict:
    """schema.org `Article` for a guide. `organization_id` is the `@id` of the Organization
    node that base.html emits on every page. No FAQPage: Google shows FAQ rich results only for
    authoritative government and health sites.

    A guide with `ai_assisted` also carries `digitalSourceType`, the CreativeWork property for
    the nature of a work's source, set to the IPTC value "composite with trained algorithmic
    media". Fields such as `creativeWorkStatus` say nothing about a work's origin: not used.
    """
    article: dict = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": guide.title,
        "description": guide.description,
        "datePublished": guide.published.isoformat(),
        "dateModified": guide.updated.isoformat(),
        "author": AUTHOR,
        "publisher": {"@id": organization_id},
        "mainEntityOfPage": canonical_url,
        "inLanguage": "it-IT",
        "isAccessibleForFree": True,
    }
    if guide.ai_assisted:
        article["digitalSourceType"] = AI_ASSISTED_SOURCE_TYPE
    return article


def breadcrumb_json_ld(trail: list[tuple[str, str]]) -> dict:
    """schema.org `BreadcrumbList` from `(name, absolute url)` pairs, outermost first."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": position, "name": name, "item": url}
            for position, (name, url) in enumerate(trail, start=1)
        ],
    }


def internal_links(guide: Guide) -> list[tuple[str, int]]:
    """Every link destination in the body that points inside the site (`/it/…`), with the
    1-based line of the source file it is on. For `scripts/check_guides.py`."""
    found: list[tuple[str, int]] = []
    for token in _MD.parse(guide.body_md, _env(frozenset())):
        if token.type != "inline":
            continue
        line = guide.body_line + (token.map[0] if token.map else 0)
        for child in token.children or []:
            if child.type == "link_open":
                href = child.attrGet("href") or ""
                if href.startswith("/it/"):
                    found.append((href, line))
    return found
