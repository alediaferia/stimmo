from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.metadata import version as _pkg_version
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi import Path as FPath
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from stimmo.data import amenities, geocode, history, neighborhoods, ntn, omi, zones
from stimmo.data.importers import immobiliare
from stimmo.i18n import (
    LANG_TO_LOCALE,
    LOCALE_TO_LANG,
    SUPPORTED_LANGS,
    _current_locale,
    fmt_eur,
    fmt_pct,
    negotiate_locale,
    ngettext,
)
from stimmo.i18n import (
    gettext as _,
)
from stimmo.models import (
    CONSTRUCTION_ERA_LABELS,
    EXPOSURE_LABELS,
    ORIENTATION_LABELS,
    OUTDOOR_LABELS,
    PROPERTY_TYPE_HINTS,
    AmenityScore,
    ConstructionEra,
    EnergyClass,
    Exposure,
    FineCondition,
    OmiCondition,
    OmiQuote,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
    derive_omi_condition,
)
from stimmo.valuation import engine
from stimmo.valuation.verdict import HIGH_TOL
from stimmo.web import labels as _labels
from stimmo.web import metrics as _metrics
from stimmo.web import ogimage as _ogimage
from stimmo.web import share as _share
from stimmo.web.share_store import SqliteShareStore

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Enable Jinja2 i18n extension with ContextVar-backed callables.
templates.env.add_extension("jinja2.ext.i18n")
templates.env.install_gettext_callables(_, ngettext, newstyle=False)

templates.env.filters["eur"] = lambda n: fmt_eur(n)
templates.env.filters["pct"] = lambda n, d=1: fmt_pct(n, digits=d)
templates.env.filters["num"] = lambda n: _fmt_num(n)
templates.env.globals["app_version"] = "v" + _pkg_version("stimmo")
templates.env.globals["render_label"] = _labels.render


@lru_cache(maxsize=256)
def _static_hash(rel_path: str) -> str:
    p = STATIC_DIR / rel_path
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def static_url(rel_path: str) -> str:
    h = _static_hash(rel_path)
    return f"/static/{rel_path}?v={h}" if h else f"/static/{rel_path}"


templates.env.globals["static_url"] = static_url


def _cf_beacon_token() -> str | None:
    """Cloudflare Web Analytics beacon token, read lazily so tests can toggle it
    via monkeypatch without needing to reimport the app module. Unset by default
    — no beacon script is emitted until the operator configures it in compose."""
    return os.environ.get("STIMMO_CF_BEACON_TOKEN")


templates.env.globals["cf_beacon_token"] = _cf_beacon_token


def _fmt_num(n: float) -> str:
    from babel.numbers import format_decimal

    return format_decimal(round(n), format="#,##0", locale=_current_locale.get())


def _semester_start_date(semester: str) -> date | None:
    """First calendar day of an OMI semester string ("2025-2" -> 2025-07-01, "2025-1" ->
    2025-01-01). Shared by _semester_months_old (freshness banner) and _omi_lastmod
    (sitemap <lastmod> for the OMI-band pages) — both need the same parse, just a
    different use of the result."""
    try:
        year_str, half_str = semester.split("-")
        return date(int(year_str), 1 if half_str == "1" else 7, 1)
    except (ValueError, AttributeError):
        return None


def _semester_months_old(semester: str) -> int:
    sem_start = _semester_start_date(semester)
    if sem_start is None:
        return 0
    today = date.today()
    return max(0, (today.year - sem_start.year) * 12 + (today.month - sem_start.month))


def _set_locale(request: Request, lang: str) -> str:
    """Resolve lang slug → locale, store on request.state and ContextVar."""
    locale = LANG_TO_LOCALE[lang]
    request.state.locale = locale
    _current_locale.set(locale)
    return locale


def _seo_urls(
    request: Request,
    lang: str,
    route: str | None = None,
    params: Mapping[str, str] | None = None,
) -> dict:
    """Build canonical + per-lang hreflang alternates for the current response.

    `route`, when given, must name an entry in `_SEO_ROUTES` — the single registry
    that this function and `_sitemap_xml` both read (see the "Crawlability" section
    below). Alternates are emitted only for the languages that entry actually lists,
    using its per-language suffix (a `str.format` template, expanded with `params`
    for parameterized routes such as zone detail's `"/zones/{code}"`). This is what
    lets a route be single-language, or use a different URL slug per language,
    without ever advertising an alternate link to a language that doesn't exist.
    A registered route rendered under a `lang` it doesn't list is refused with 404
    rather than fabricating a broken canonical.

    `route=None` (the default) preserves the pre-registry behaviour: strip the
    current `/{lang}` prefix and re-prefix the *same* suffix for every supported
    language. That's only correct for lang-invariant paths, so every endpoint
    relying on it is required to be listed in `_SEO_MIRRORED_ENDPOINTS` as an
    explicit, reviewed opt-in — see `test_every_html_endpoint_has_a_seo_decision`
    in tests/test_seo.py.

    Uses the request's own scheme/host (which reflects https once uvicorn is
    told to trust the cloudflared proxy headers, see server.py) so this works
    both in production and in local/test runs without hardcoding the origin.
    """
    scheme = request.url.scheme
    host = request.url.netloc
    fmt_params = params or {}

    if route is not None:
        entry = _SEO_ROUTES[route]  # KeyError on a typo'd key is deliberate: fail loud.
        if lang not in entry.suffixes:
            raise HTTPException(status_code=404)
        suffixes = {lg: sfx.format(**fmt_params) for lg, sfx in entry.suffixes.items()}
    else:
        path = request.url.path
        prefix = f"/{lang}"
        suffix = path[len(prefix) :] if path.startswith(prefix) else path
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        suffixes = {lg: suffix for lg in sorted(SUPPORTED_LANGS)}

    def _abs(other_lang: str) -> str:
        return f"{scheme}://{host}/{other_lang}{suffixes[other_lang]}"

    alternates = {lg: _abs(lg) for lg in suffixes}
    default_lang = "it" if "it" in alternates else next(iter(alternates))

    return {
        "canonical_url": _abs(lang),
        "hreflang_alternates": alternates,
        "hreflang_x_default": alternates[default_lang],
    }


def _tpl(
    request: Request,
    template: str,
    ctx: dict | None = None,
    *,
    seo_route: str | None = None,
    seo_params: Mapping[str, str] | None = None,
) -> HTMLResponse:
    """Render a template with locale + SEO context merged in.

    Pass `seo_route` (a key in `_SEO_ROUTES`) for indexable pages, plus `seo_params`
    for parameterized ones (e.g. `seo_params={"code": code}` for zone detail), so
    canonical/hreflang reflect that route's real per-language suffixes. Omit both
    only for endpoints listed in `_SEO_MIRRORED_ENDPOINTS`. See `_seo_urls`.
    """
    locale = getattr(request.state, "locale", "it_IT")
    lang = LOCALE_TO_LANG.get(locale, "it")
    base: dict = {
        "lang": lang,
        "locale": locale,
        **_seo_urls(request, lang, route=seo_route, params=seo_params),
    }
    if ctx:
        base.update(ctx)
    return templates.TemplateResponse(request, template, base)


# ---------------------------------------------------------------------------
# Share-link store
#
# Constructed once at module import time from the STIMMO_SHARE_DB env var.
# Defaults to var/share.db (repo-local, git-ignored) when the env var is unset.
# Tests inject their own SqliteShareStore (tmp_path or ":memory:") via the
# module-level _share_store reference — see tests/test_share_store.py.
# ---------------------------------------------------------------------------
_share_db_path = os.environ.get(
    "STIMMO_SHARE_DB",
    str(Path(__file__).parent.parent.parent.parent / "var" / "share.db"),
)
# Ensure the parent directory exists (for the default var/ path; STIMMO_SHARE_DB
# paths are the operator's responsibility).
Path(_share_db_path).parent.mkdir(parents=True, exist_ok=True)
_share_store: SqliteShareStore = SqliteShareStore(_share_db_path)

_IMPORT_REGISTRY = {"immobiliare": immobiliare.parse}

# ── MCP: initialise before FastAPI app so the lifespan can wire the session manager ──
from contextlib import asynccontextmanager  # noqa: E402

from stimmo.mcp.server import build_mcp_app as _build_mcp_app  # noqa: E402
from stimmo.mcp.server import get_session_manager as _get_mcp_sm  # noqa: E402

_mcp_asgi_app = _build_mcp_app()


@asynccontextmanager
async def _lifespan(_app):
    # Force the neighborhood content file to load now rather than lazily on first
    # request. A missing file degrades silently (empty blurbs — the normal public-repo
    # state); a malformed one raises ValueError (see neighborhoods._load_blurb_content).
    # Doing this at startup means a malformed STIMMO_CONTENT_DIR/neighborhoods.json
    # aborts the deploy instead of quietly 500-ing the sitemap and every neighborhood
    # page while the process still passes its health check.
    neighborhoods.list_neighborhoods()
    async with _get_mcp_sm().run():
        yield


app = FastAPI(title="stimmo — Milan fair-price estimator", lifespan=_lifespan)

STATIC_DIR = Path(__file__).parent / "static"


class _CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", _CachedStaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Locale-neutral API endpoints
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _zones_geojson_payload() -> str:
    asset = Path(__file__).parent.parent / "data" / "assets" / "milano_omi_zones.geojson"
    with asset.open() as fh:
        gj = json.load(fh)
    prices = omi.zone_price_index()
    for feat in gj.get("features", []):
        props = feat.setdefault("properties", {})
        code = str(props.get("CODZONA", ""))
        band = prices.get(code)
        if band is not None:
            props["eur_m2_min"], props["eur_m2_max"] = band
            props["eur_m2_mid"] = (band[0] + band[1]) / 2
        else:
            props["eur_m2_min"] = props["eur_m2_max"] = props["eur_m2_mid"] = None
    return json.dumps(gj)


@app.get("/api/geocode")
def api_geocode(q: str) -> Response:
    q = q.strip()
    if len(q) < 3:
        return Response(
            content=json.dumps({"error": "query too short"}),
            media_type="application/json",
            status_code=400,
        )
    try:
        lat, lon = geocode.geocode(q)
    except LookupError:
        return Response(
            content=json.dumps({"found": False}),
            media_type="application/json",
            status_code=404,
        )
    except Exception as e:
        return Response(
            content=json.dumps({"error": str(e)}),
            media_type="application/json",
            status_code=502,
        )
    z = zones.zone_for_point(lat, lon)
    return Response(
        content=json.dumps(
            {
                "found": True,
                "lat": lat,
                "lon": lon,
                "zone_code": z[0] if z else None,
                "zone_descr": z[1] if z else None,
            }
        ),
        media_type="application/json",
    )


@app.get("/api/zones.geojson")
def zones_geojson() -> Response:
    return Response(
        content=_zones_geojson_payload(),
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# Crawlability: robots.txt + sitemap.xml
#
# Locale-neutral, no-lang-prefix routes. Must be registered before
# bare_path_redirect (the /{path:path} catch-all further down) — FastAPI/
# Starlette route matching is order-sensitive.
# ---------------------------------------------------------------------------

SITE_ORIGIN = "https://stimmo.it"

# Being readable by AI crawlers is a deliberate distribution channel for this
# product (see docs/distribution-plan.md) — allow everything, search and AI.
_ROBOTS_TXT = """User-agent: *
Allow: /

User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: CCBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: meta-externalagent
Allow: /

Sitemap: https://stimmo.it/sitemap.xml
"""


@app.api_route("/robots.txt", methods=["GET", "HEAD"])
def robots_txt() -> Response:
    return Response(content=_ROBOTS_TXT, media_type="text/plain")


# ---------------------------------------------------------------------------
# SEO route registry — the single source of truth for both per-page
# canonical/hreflang tags (_seo_urls, used by _tpl) and sitemap.xml (_sitemap_xml).
#
# Each entry maps lang -> path suffix (the part of the URL after "/{lang}") for
# only the languages that route actually exists in. A route that's missing a
# language here will never advertise an alternate link to it, and can't be
# rendered under it either (_seo_urls 404s rather than fabricate one).
#
# Suffixes may be str.format templates ("/zones/{code}") for parameterized
# routes; the handler must then pass the same param(s) via `_tpl(..., seo_params=...)`
# so every language's suffix can be expanded consistently. `expand()` supplies
# those params for the sitemap — one dict per instance of the route (e.g. one per
# OMI zone code); static routes leave it at the default (a single empty dict).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeoRoute:
    key: str
    endpoint: str  # FastAPI handler function name — see _SEO_MIRRORED_ENDPOINTS below
    suffixes: Mapping[str, str]
    expand: Callable[[], Iterable[Mapping[str, str]]] = lambda: ({},)
    # Derives <lastmod> from whatever actually determines this URL's content — never
    # from build/deploy time, which would bump every URL together on every release
    # regardless of what changed (see the three concrete suppliers below). Returns
    # None to omit <lastmod> for that URL entirely: a missing date is a smaller lie
    # to a crawler than a wrong-but-stable one, so "we don't know" must stay a real,
    # distinguishable outcome rather than falling back to some other guess.
    lastmod: Callable[[Mapping[str, str]], date | None] | None = None


_SEO_ROUTES: dict[str, SeoRoute] = {}


def _register_seo_route(
    key: str,
    endpoint: str,
    suffixes: Mapping[str, str],
    *,
    expand: Callable[[], Iterable[Mapping[str, str]]] | None = None,
    lastmod: Callable[[Mapping[str, str]], date | None] | None = None,
) -> SeoRoute:
    route = SeoRoute(
        key=key,
        endpoint=endpoint,
        suffixes=dict(suffixes),
        expand=expand or (lambda: ({},)),
        lastmod=lastmod,
    )
    _SEO_ROUTES[key] = route
    # The registry is only ever mutated by module-level calls to this function, all
    # of which run at import time, before any request and before _sitemap_xml is
    # ever called — so caching it is safe. This clear is a belt-and-braces guarantee
    # of that, not a workaround: it means a *future* runtime registration can never
    # silently serve a stale sitemap, without anyone having to remember why.
    _sitemap_xml.cache_clear()
    return route


# ---------------------------------------------------------------------------
# <lastmod> suppliers — one per "what determines this URL's content" story.
# Each is real, versioned, already-bundled data; none is build/deploy time or a
# file mtime (git doesn't preserve those, and the Dockerfile's COPY stamps build
# time onto them regardless — both would produce a stable-looking but false date).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _release_date() -> date | None:
    """Date of the most recent version heading in CHANGELOG.md
    ("## vX.Y.Z (YYYY-MM-DD)"). CHANGELOG.md is a committed, bundled file — this is
    the same kind of real, stable data as app_version's _pkg_version() lookup just
    above, not a proxy for "when was this specific page last touched". Returns None
    if the file is missing or its heading format ever changes underneath this regex.
    """
    changelog = Path(__file__).parent.parent.parent.parent / "CHANGELOG.md"
    try:
        text = changelog.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    m = re.search(r"^## v\d+\.\d+\.\d+ \((\d{4}-\d{2}-\d{2})\)", text, re.MULTILINE)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _release_lastmod(_params: Mapping[str, str]) -> date | None:
    """SeoRoute.lastmod for pages that are hand-written template markup with no
    finer-grained per-page timestamp of their own (form, about, bookmarklet,
    privacy) — the release date is the best real signal available for them.
    Ignores params; every route using this shares one process-wide release date.
    """
    return _release_date()


def _omi_lastmod(_params: Mapping[str, str]) -> date | None:
    """Start-of-semester date for the OMI vintage currently bundled
    (data.omi.semester(), e.g. "2025-2" -> 2025-07-01). Used for zones_index and
    zone_detail: their entire content is exactly this semester's €/m² band, so
    refreshing OMI (scripts/refresh_omi.py) is the one real event that changes what
    these pages say — and is therefore the one honest answer to "when did this
    page's content last change". Same param shape (ignores zone code) for both
    routes since every zone shares one bundled vintage.
    """
    return _semester_start_date(omi.semester())


def _neighborhood_lastmod(params: Mapping[str, str]) -> date | None:
    """The neighborhood's own `updated` date from the external content file (see
    data/neighborhoods.py's Neighborhood.updated), parsed from the params the
    sitemap already expands neighborhood_detail with (slug_en). Omits <lastmod>
    (returns None) when the content file predates this field or a neighborhood's
    entry doesn't carry one — this is the common case today (see CHANGELOG/report:
    only the first batch has it), and is exactly the "don't know, don't guess" case
    SeoRoute.lastmod exists to allow.
    """
    n = neighborhoods.neighborhood_for_slug(params["slug_en"], "en")
    if n is None or not n.updated:
        return None
    try:
        return date.fromisoformat(n.updated)
    except ValueError:
        return None


def _sitemap_url_block(route: SeoRoute, params: Mapping[str, str]) -> str:
    langs = sorted(route.suffixes)
    locs = {lg: f"{SITE_ORIGIN}/{lg}{route.suffixes[lg].format(**params)}" for lg in langs}
    default_lang = "it" if "it" in locs else langs[0]
    lastmod = route.lastmod(params) if route.lastmod else None
    lastmod_tag = f"\n    <lastmod>{lastmod.isoformat()}</lastmod>" if lastmod else ""
    blocks = []
    for lg in langs:
        alt_links = "\n".join(
            f'    <xhtml:link rel="alternate" hreflang="{alt_lang}" href="{alt_loc}"/>'
            for alt_lang, alt_loc in locs.items()
        )
        alt_links += (
            f'\n    <xhtml:link rel="alternate" hreflang="x-default" href="{locs[default_lang]}"/>'
        )
        blocks.append(f"  <url>\n    <loc>{locs[lg]}</loc>{lastmod_tag}\n{alt_links}\n  </url>")
    return "\n".join(blocks)


@lru_cache(maxsize=1)
def _sitemap_xml() -> str:
    entries: list[str] = [
        _sitemap_url_block(route, params)
        for route in _SEO_ROUTES.values()
        for params in route.expand()
    ]
    body = "\n".join(entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        f"{body}\n"
        "</urlset>\n"
    )


# Concrete registrations. Order here is the order URLs appear in the sitemap.
# _register_seo_route calls _sitemap_xml.cache_clear(), so this must come after
# _sitemap_xml's def above (a plain top-level name lookup, resolved at call time —
# see the comment inside _register_seo_route).
_register_seo_route("form", "form", {"it": "/", "en": "/"}, lastmod=_release_lastmod)
_register_seo_route("about", "about", {"it": "/about", "en": "/about"}, lastmod=_release_lastmod)
_register_seo_route(
    "bookmarklet",
    "bookmarklet_page",
    {"it": "/bookmarklet", "en": "/bookmarklet"},
    lastmod=_release_lastmod,
)
_register_seo_route(
    "privacy", "privacy", {"it": "/privacy", "en": "/privacy"}, lastmod=_release_lastmod
)
_register_seo_route(
    "zones_index", "zones_index", {"it": "/zones", "en": "/zones"}, lastmod=_omi_lastmod
)
_register_seo_route(
    "zone_detail",
    "zone_detail",
    {"it": "/zones/{code}", "en": "/zones/{code}"},
    expand=lambda: ({"code": code} for code, _descr in zones.list_zones()),
    lastmod=_omi_lastmod,
)
_register_seo_route(
    "neighborhood_detail",
    "neighborhood_detail",
    {
        "it": "/milano/{slug_it}-prezzi-al-mq",
        "en": "/milan/{slug_en}-property-prices",
    },
    # Staggered launch (see Neighborhood.blurb_it/blurb_en in data/neighborhoods.py):
    # a neighborhood only enters the sitemap once BOTH language blurbs are filled in,
    # so we never ask Google to index a page that's thin/near-duplicate in either
    # language. The route resolves and renders regardless — see neighborhood_detail
    # further down — so it stays reachable via the zone pages/zones index links.
    lastmod=_neighborhood_lastmod,
    expand=lambda: (
        {"slug_it": n.slug_it, "slug_en": n.slug_en}
        for n in neighborhoods.list_neighborhoods()
        if n.blurb_it and n.blurb_en
    ),
)


def neighborhood_url(n: neighborhoods.Neighborhood, lang: str) -> str:
    """Canonical per-language URL for a neighborhood page.

    Built from the same suffix templates registered above (rather than duplicating
    "/milano/...-prezzi-al-mq" / "/milan/...-property-prices" literals in every
    template that links to a neighborhood), so link targets can never drift from
    the actual route / sitemap definitions.
    """
    suffix = _SEO_ROUTES["neighborhood_detail"].suffixes[lang]
    return f"/{lang}{suffix.format(slug_it=n.slug_it, slug_en=n.slug_en)}"


templates.env.globals["neighborhood_url"] = neighborhood_url

# HTML-rendering endpoints that deliberately opt into _seo_urls's pre-registry
# fallback (same suffix mirrored across every supported language) instead of a
# registry entry — because they're not indexable content pages (import wizard,
# estimate results, share links). Every endpoint with response_class=HTMLResponse
# must appear either here or as a registered SeoRoute.endpoint above; see
# test_every_html_endpoint_has_a_seo_decision in tests/test_seo.py.
_SEO_MIRRORED_ENDPOINTS: frozenset[str] = frozenset(
    {"import_get", "import_post", "estimate", "share_view", "share_view_short"}
)


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
def sitemap_xml() -> Response:
    return Response(content=_sitemap_xml(), media_type="application/xml")


# ---------------------------------------------------------------------------
# Locale negotiation for entry-point redirects
# ---------------------------------------------------------------------------


@app.api_route("/", methods=["GET", "HEAD"])
def root_redirect(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    return RedirectResponse(f"/{lang}/", status_code=301)


@app.get("/import")
def import_redirect_get(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    qs = request.url.query
    target = f"/{lang}/import"
    if qs:
        target += f"?{qs}"
    return RedirectResponse(target, status_code=302)


@app.post("/import")
async def import_redirect_post(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    qs = request.url.query
    target = f"/{lang}/import"
    if qs:
        target += f"?{qs}"
    # 308 preserves the POST body through the redirect
    return RedirectResponse(target, status_code=308)


@app.api_route("/about", methods=["GET", "HEAD"])
def about_redirect(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    return RedirectResponse(f"/{lang}/about", status_code=301)


@app.api_route("/bookmarklet", methods=["GET", "HEAD"])
def bookmarklet_redirect(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    return RedirectResponse(f"/{lang}/bookmarklet", status_code=301)


@app.post("/set-lang")
def set_lang(
    request: Request,
    lang: str = Form(...),
    next: str = Form("/"),
) -> RedirectResponse:
    if lang not in SUPPORTED_LANGS:
        lang = "it"

    # Reject off-origin next paths.
    parsed = urlparse(next)
    if parsed.netloc or parsed.scheme or not next.startswith("/"):
        next = f"/{lang}/"

    response = RedirectResponse(next, status_code=302)
    response.set_cookie(
        "stimmo_lang",
        lang,
        max_age=31536000,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


# ---------------------------------------------------------------------------
# Localized page routes  /{lang}/...
# ---------------------------------------------------------------------------

_LANG_RE = f"^({'|'.join(SUPPORTED_LANGS)})$"


def _replay_fields(**fields: object) -> list[tuple[str, str]]:
    """Serialize estimate-endpoint params as (name, value) pairs for hidden inputs."""
    out: list[tuple[str, str]] = []
    for name, val in fields.items():
        if val is None or val == "":
            continue
        out.append((name, str(val)))
    return out


def _form_context(
    request: Request,
    defaults_override: dict | None = None,
    import_source: str | None = None,
) -> dict:
    defaults = {
        "property_type": PropertyType.CIVILI.value,
        "fine_condition": FineCondition.ABITABILE.value,
        "outdoor": Outdoor.NONE.value,
        "construction_era": ConstructionEra.POSTWAR_BOOM.value,
        "orientation": Orientation.MIXED.value,
        "floor": 2,
        "total_floors": 5,
        "has_lift": True,
        "has_box": False,
        "has_second_bathroom": False,
        "surface_m2": "",
        "asking_price_eur": "",
        "address": "",
        "energy_class": "",
        "exposure": Exposure.STREET.value,
        "room_count": "",
    }
    if defaults_override:
        defaults.update({k: v for k, v in defaults_override.items() if v is not None})

    return {
        "property_types": [
            {"value": e.value, "hint": PROPERTY_TYPE_HINTS[e]} for e in PropertyType
        ],
        "fine_conditions": [e.value for e in FineCondition],
        "energy_classes": ["", *[e.value for e in EnergyClass]],
        "outdoors": [{"value": e.value, "label": OUTDOOR_LABELS[e]} for e in Outdoor],
        "construction_eras": [
            {"value": e.value, "label": CONSTRUCTION_ERA_LABELS[e]} for e in ConstructionEra
        ],
        "orientations": [{"value": e.value, "label": ORIENTATION_LABELS[e]} for e in Orientation],
        "exposures": [{"value": e.value, "label": EXPOSURE_LABELS[e]} for e in Exposure],
        "defaults": defaults,
        "import_source": import_source,
        "omi_semester": omi.semester(),
        "zone_count": len(omi.available_zones()),
    }


@app.get("/{lang}/about", response_class=HTMLResponse)
def about(request: Request, lang: str = FPath(pattern=_LANG_RE)) -> HTMLResponse:
    _set_locale(request, lang)
    return _tpl(
        request,
        "about.html",
        {
            "semester": omi.semester(),
            "zone_count": len(omi.available_zones()),
        },
        seo_route="about",
    )


@app.get("/{lang}/privacy", response_class=HTMLResponse)
def privacy(request: Request, lang: str = FPath(pattern=_LANG_RE)) -> HTMLResponse:
    _set_locale(request, lang)
    return _tpl(request, "privacy.html", seo_route="privacy")


# ---------------------------------------------------------------------------
# WP-7: programmatic OMI zone pages (docs/distribution-plan.md, Phase C).
#
# Display-only pages sourced entirely from bundled data (zones.list_zones(),
# omi.zone_quotes/zone_price_index, history.series). No pricing/adjustment
# logic here — that stays in valuation/adjustments.py, per the tuning-surface
# invariant.
# ---------------------------------------------------------------------------

_ZONE_CODE_RE = r"^[A-Za-z0-9]{1,10}$"


@app.get("/{lang}/zones", response_class=HTMLResponse)
def zones_index(request: Request, lang: str = FPath(pattern=_LANG_RE)) -> HTMLResponse:
    _set_locale(request, lang)
    price_index = omi.zone_price_index()
    fascia_index = omi.zone_fascia_index()

    groups: dict[str, list[dict]] = {}
    for code, descr in zones.list_zones():
        fascia = fascia_index.get(code, code[0])
        band = price_index.get(code)
        groups.setdefault(fascia, []).append(
            {
                "code": code,
                "descr": descr,
                "eur_m2_min": band[0] if band else None,
                "eur_m2_max": band[1] if band else None,
                "neighborhoods": neighborhoods.neighborhoods_for_zone(code),
            }
        )
    for group in groups.values():
        group.sort(key=lambda z: z["code"])

    return _tpl(
        request,
        "zones_index.html",
        {
            "fascia_groups": sorted(groups.items()),
            "zone_count": sum(len(g) for g in groups.values()),
            "semester": omi.semester(),
        },
        seo_route="zones_index",
    )


@app.get("/{lang}/zones/{code}", response_class=HTMLResponse)
def zone_detail(
    request: Request,
    lang: str = FPath(pattern=_LANG_RE),
    code: str = FPath(pattern=_ZONE_CODE_RE),
) -> HTMLResponse:
    _set_locale(request, lang)
    zone_names = dict(zones.list_zones())
    if code not in zone_names:
        raise HTTPException(status_code=404, detail="Unknown OMI zone")

    fascia = omi.zone_fascia_index().get(code, code[0])
    return _tpl(
        request,
        "zone_detail.html",
        {
            "zone_code": code,
            "zone_name": zone_names[code],
            "fascia": fascia,
            "quotes": omi.zone_quotes(code),
            "history_series": history.series(code, PropertyType.CIVILI, OmiCondition.NORMALE),
            "semester": omi.semester(),
            "parent_neighborhoods": neighborhoods.neighborhoods_for_zone(code),
        },
        seo_route="zone_detail",
        seo_params={"code": code},
    )


# ---------------------------------------------------------------------------
# WP-8: neighborhood price pages — colloquial-name landing pages layered on top
# of the OMI zone pages above via data/neighborhoods.py.
#
# Per-language slugs mean the it/en URLs don't share a path shape ("/milano/
# {slug}-prezzi-al-mq" vs "/milan/{slug}-property-prices"), so this needs two
# literal FastAPI route templates — registered on the SAME view function via two
# stacked decorators below, so there's exactly one physical endpoint name and
# exactly one _SEO_ROUTES entry (see test_every_html_endpoint_has_a_seo_decision
# in tests/test_seo.py). `lang` is recovered from the matched literal prefix,
# not a path parameter — no {lang} placeholder exists in either template.
#
# No pricing logic here: _neighborhood_price_band/_neighborhood_midpoint only
# read data/omi.py's already-computed OMI bands and take min/max/mean across a
# neighborhood's zone(s) for display — the tuning surface stays entirely in
# valuation/adjustments.py.
# ---------------------------------------------------------------------------

_NEIGHBORHOOD_SLUG_RE = r"^[a-z0-9]+(-[a-z0-9]+)*$"


def _neighborhood_price_band(n: neighborhoods.Neighborhood) -> tuple[float, float] | None:
    """Min-of-mins / max-of-maxes €/m² (Abitazioni civili, condition NORMALE) across
    a neighborhood's OMI zone(s). None if none of its zones have a bundled quote."""
    price_index = omi.zone_price_index()
    bands = [price_index[code] for code in n.zone_codes if code in price_index]
    if not bands:
        return None
    return min(b[0] for b in bands), max(b[1] for b in bands)


def _neighborhood_midpoint(n: neighborhoods.Neighborhood) -> float | None:
    band = _neighborhood_price_band(n)
    return (band[0] + band[1]) / 2 if band else None


def _nearby_neighborhoods(n: neighborhoods.Neighborhood, count: int = 3) -> list[dict]:
    """The `count` other curated neighborhoods whose €/m² midpoint sits closest to
    `n`'s.

    Picked by price proximity rather than zone-polygon adjacency: stimmo has no
    neighborhood-to-neighborhood adjacency graph (only zone polygons), and "closest
    by price" is exactly the comparison a buyer weighing this neighborhood wants —
    "where else, at a similar level, should I also look?"
    """
    target = _neighborhood_midpoint(n)
    if target is None:
        return []
    scored: list[tuple[float, neighborhoods.Neighborhood, float]] = []
    for other in neighborhoods.list_neighborhoods():
        if other is n:
            continue
        mid = _neighborhood_midpoint(other)
        if mid is not None:
            scored.append((abs(mid - target), other, mid))
    scored.sort(key=lambda s: s[0])
    return [{"neighborhood": other, "eur_m2_mid": mid} for _dist, other, mid in scored[:count]]


def _render_neighborhood_detail(request: Request, lang: str, slug: str) -> HTMLResponse:
    _set_locale(request, lang)
    n = neighborhoods.neighborhood_for_slug(slug, lang)
    if n is None:
        raise HTTPException(status_code=404, detail="Unknown neighborhood")

    zone_names = dict(zones.list_zones())
    return _tpl(
        request,
        "neighborhood_detail.html",
        {
            "n": n,
            "band": _neighborhood_price_band(n),
            "spans_multiple_zones": len(n.zone_codes) > 1,
            "city_avg_eur_m2": omi.citywide_average(),
            "nearby": _nearby_neighborhoods(n),
            "shared_zones": neighborhoods.shared_zones(n),
            "zone_names": zone_names,
            "blurb": n.blurb_it if lang == "it" else n.blurb_en,
            "semester": omi.semester(),
        },
        seo_route="neighborhood_detail",
        seo_params={"slug_it": n.slug_it, "slug_en": n.slug_en},
    )


@app.get("/it/milano/{slug}-prezzi-al-mq", response_class=HTMLResponse)
@app.get("/en/milan/{slug}-property-prices", response_class=HTMLResponse)
def neighborhood_detail(
    request: Request, slug: str = FPath(pattern=_NEIGHBORHOOD_SLUG_RE)
) -> HTMLResponse:
    lang = "it" if request.url.path.startswith("/it/") else "en"
    return _render_neighborhood_detail(request, lang, slug)


@app.get("/{lang}/", response_class=HTMLResponse)
def form(request: Request, lang: str = FPath(pattern=_LANG_RE)) -> HTMLResponse:
    _set_locale(request, lang)
    ctx = _form_context(request)
    return _tpl(request, "form.html", ctx, seo_route="form")


def _find_listing(node: dict | list | None, depth: int = 0) -> dict | None:
    if depth > 14 or not node:
        return None
    if isinstance(node, dict) and "price" in node and "location" in node and "typology" in node:
        return node
    if isinstance(node, list):
        for x in node:
            hit = _find_listing(x, depth + 1)
            if hit:
                return hit
    elif isinstance(node, dict):
        for v in node.values():
            hit = _find_listing(v, depth + 1)
            if hit:
                return hit
    return None


@app.get("/{lang}/import", response_class=HTMLResponse)
def import_get(
    request: Request,
    lang: str = FPath(pattern=_LANG_RE),
    src: str = "",
    v: str = "1",
    p: str = "",
) -> HTMLResponse:
    _set_locale(request, lang)
    if src not in _IMPORT_REGISTRY or not p:
        return _error(request, [_("Invalid import request")])

    try:
        padded = p + "=" * (-len(p) % 4)
        raw = base64.urlsafe_b64decode(padded)
        if len(raw) > 32_768:
            return _error(request, [_("Payload too large")])
        payload = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return _error(request, [_("Malformed import payload")])

    prefill = _IMPORT_REGISTRY[src](payload)
    ctx = _form_context(request, prefill, import_source=src)
    return _tpl(request, "form.html", ctx)


@app.post("/{lang}/import", response_class=HTMLResponse)
def import_post(
    request: Request,
    lang: str = FPath(pattern=_LANG_RE),
    src: str = Form("immobiliare"),
    html: str = Form(""),
) -> HTMLResponse:
    _set_locale(request, lang)
    if src not in _IMPORT_REGISTRY or not html:
        return _error(request, [_("Missing source or HTML content")])

    if len(html) > 256_000:
        return _error(request, [_("Pasted HTML too large")])

    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return _error(request, [_("Could not find listing data in pasted HTML")])

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return _error(request, [_("Could not parse listing data")])

    listing = _find_listing(data)
    if not listing:
        return _error(request, [_("Could not locate listing fields in pasted data")])

    prefill = _IMPORT_REGISTRY[src](listing)
    ctx = _form_context(request, prefill, import_source=src)
    return _tpl(request, "form.html", ctx)


@app.get("/{lang}/bookmarklet", response_class=HTMLResponse)
def bookmarklet_page(request: Request, lang: str = FPath(pattern=_LANG_RE)) -> HTMLResponse:
    _set_locale(request, lang)
    js_path = STATIC_DIR / "bookmarklet.js"
    if not js_path.exists():
        return _error(request, [_("Bookmarklet not available")])

    js_src = js_path.read_text()
    stimmo_base = str(request.base_url).rstrip("/")
    alert_str = _("stimmo: could not find listing data on this page")
    js_src = js_src.replace("'https://stimmo.it'", f"'{stimmo_base}'")
    js_src = js_src.replace("'__STIMMO_LANG__'", f"'{lang}'")
    js_src = js_src.replace("'__STIMMO_ALERT__'", json.dumps(alert_str))

    bookmarklet_href = "javascript:" + re.sub(r"\s+", " ", js_src).strip()
    return _tpl(
        request, "bookmarklet.html", {"bookmarklet_href": bookmarklet_href}, seo_route="bookmarklet"
    )


def _build_result_context(
    request: Request,
    lang: str,
    prop: Property,
    lat: float,
    lon: float,
    amen: AmenityScore,
    quote: OmiQuote,
    zone_code: str,
    amenity_status: str = "ok",
    amenity_error: str | None = None,
) -> dict:
    """Build the full template context for result.html from resolved inputs.

    Shared by the live estimate POST path and the share-token GET path so the
    engine, history, NTN, gauge, and share-URL logic stay in one place.  Callers
    own the zone lookup and OMI quote fetch (their error handling differs) and
    pass the resolved ``quote``/``zone_code`` in.
    """
    est = engine.estimate(prop, quote, amen)
    series = history.series(zone_code, prop.property_type, prop.omi_condition)
    ntn_total = ntn.total_quarters(last_n=8)
    bucket_label, ntn_bucket = ntn.by_bucket_quarters(prop.surface_m2, last_n=8)
    bucket_by_q = {p_.quarter: p_.ntn for p_ in ntn_bucket}

    asking = prop.asking_price_eur
    verdict_high = est.ask_range_high_eur * HIGH_TOL
    g_lo = min(est.ask_range_low_eur, est.range_low_eur) * 0.92
    g_hi = max(verdict_high, asking, est.range_high_eur) * 1.06

    def _x(v: float) -> float:
        return (v - g_lo) / (g_hi - g_lo) * 100

    gauge = {
        "omi_band": {
            "left": _x(est.range_low_eur),
            "width": _x(est.range_high_eur) - _x(est.range_low_eur),
        },
        "ask_band": {
            "left": _x(est.ask_range_low_eur),
            "width": _x(verdict_high) - _x(est.ask_range_low_eur),
        },
        "ticks": [
            {"label": _("OMI low"), "value": est.range_low_eur, "x": _x(est.range_low_eur)},
            {"label": _("Ask low"), "value": est.ask_range_low_eur, "x": _x(est.ask_range_low_eur)},
            {"label": _("Ask mid"), "value": est.ask_range_mid_eur, "x": _x(est.ask_range_mid_eur)},
            {"label": _("OMI high"), "value": est.range_high_eur, "x": _x(est.range_high_eur)},
            {"label": _("Ask high"), "value": verdict_high, "x": _x(verdict_high)},
        ],
        "asking_x": _x(asking),
    }

    # Build absolute share URL (works behind Cloudflare: use request.base_url for scheme+host).
    # New path: store the blob and emit a short /s/<id> link (no /{lang}/ prefix, D4).
    # The legacy long-token encode() is retained for the fallback decode path (D5).
    blob = _share.encode_blob(prop, lat, lon, amen)
    share_id = _share_store.put(blob)
    base = str(request.base_url).rstrip("/")
    share_url = f"{base}/s/{share_id}"
    og_image_url = f"{base}/og/{share_id}.png"
    # Legacy token kept for the retained /{lang}/s/{token} route (D5).
    token = _share.encode(prop, lat, lon, amen)

    return {
        "p": prop,
        "est": est,
        "lat": lat,
        "lon": lon,
        "history_series": series,
        "ntn_total": ntn_total,
        "bucket_label": bucket_label,
        "bucket_by_q": bucket_by_q,
        "amenity_status": amenity_status,
        "amenity_error": amenity_error,
        "semester_months_old": _semester_months_old(est.omi_quote.semester),
        "gauge": gauge,
        "share_url": share_url,
        "og_image_url": og_image_url,
        "token": token,
    }


@app.post("/{lang}/estimate", response_class=HTMLResponse)
def estimate(
    request: Request,
    lang: str = FPath(pattern=_LANG_RE),
    address: str = Form(...),
    surface_m2: float = Form(...),
    property_type: str = Form(...),
    fine_condition: str = Form(...),
    floor: int = Form(...),
    total_floors: int = Form(...),
    has_lift: str = Form("off"),
    energy_class: str = Form(""),
    outdoor: str = Form(Outdoor.NONE.value),
    has_box: str = Form("off"),
    construction_era: str = Form(ConstructionEra.POSTWAR_BOOM.value),
    orientation: str = Form(Orientation.MIXED.value),
    exposure: str = Form(Exposure.STREET.value),
    has_second_bathroom: str = Form("off"),
    room_count: int | None = Form(None),
    asking_price_eur: float = Form(...),
    omi_condition_override: str = Form(""),
) -> HTMLResponse:
    _set_locale(request, lang)

    try:
        fine = FineCondition(fine_condition)
        if omi_condition_override:
            omi_cond = OmiCondition(omi_condition_override)
        else:
            omi_cond = derive_omi_condition(fine)
        prop = Property(
            address=address.strip(),
            surface_m2=surface_m2,
            property_type=PropertyType(property_type),
            omi_condition=omi_cond,
            fine_condition=fine,
            floor=floor,
            total_floors=total_floors,
            has_lift=has_lift == "on",
            energy_class=EnergyClass(energy_class) if energy_class else None,
            outdoor=Outdoor(outdoor),
            has_box=has_box == "on",
            construction_era=ConstructionEra(construction_era),
            orientation=Orientation(orientation),
            exposure=Exposure(exposure),
            has_second_bathroom=has_second_bathroom == "on",
            room_count=room_count if room_count and room_count > 0 else None,
            asking_price_eur=asking_price_eur,
        )
    except (ValidationError, ValueError) as e:
        return _error(request, [str(e)])

    try:
        lat, lon = geocode.geocode(prop.address)
    except LookupError as e:
        return _error(request, [_("Geocoding failed: %(e)s") % {"e": e}])

    z = zones.zone_for_point(lat, lon)
    if z is None:
        return _error(request, [_("Address is outside the Milano comune — no OMI zone.")])
    zone_code, zone_name = z

    if not omi_condition_override:
        available = omi.available_conditions(zone_code, prop.property_type)
        if available and prop.omi_condition not in available:
            return _tpl(
                request,
                "omi_alternatives.html",
                {
                    "requested_condition": prop.omi_condition.value,
                    "fine_condition": prop.fine_condition.value,
                    "zone_code": zone_code,
                    "zone_name": zone_name,
                    "available_conditions": [c.value for c in available],
                    "form_fields": _replay_fields(
                        address=prop.address,
                        surface_m2=surface_m2,
                        property_type=property_type,
                        fine_condition=fine_condition,
                        floor=floor,
                        total_floors=total_floors,
                        has_lift=has_lift,
                        energy_class=energy_class,
                        outdoor=outdoor,
                        has_box=has_box,
                        construction_era=construction_era,
                        orientation=orientation,
                        exposure=exposure,
                        has_second_bathroom=has_second_bathroom,
                        room_count=room_count,
                        asking_price_eur=asking_price_eur,
                    ),
                },
            )

    try:
        quote = omi.lookup(zone_code, prop.property_type, prop.omi_condition)
    except LookupError as e:
        return _error(request, [_("OMI lookup failed: %(e)s") % {"e": e}])
    quote.zone_descr = zone_name

    amenity_status = "ok"
    amenity_error: str | None = None
    try:
        amen = amenities.fetch_amenities(lat, lon)
    except Exception as e:
        amenity_status = "failed"
        amenity_error = str(e)
        amen = AmenityScore()

    ctx = _build_result_context(
        request,
        lang,
        prop,
        lat,
        lon,
        amen,
        quote,
        zone_code,
        amenity_status=amenity_status,
        amenity_error=amenity_error,
    )
    return _tpl(request, "result.html", ctx)


@app.get("/{lang}/s/{token}", response_class=HTMLResponse)
def share_view(
    request: Request,
    lang: str = FPath(pattern=_LANG_RE),
    token: str = FPath(min_length=1),
) -> HTMLResponse:
    """Decode a share token and render result.html without hitting live services.

    Legacy route retained indefinitely (D5).  New short links use share_view_short.
    """
    _set_locale(request, lang)
    try:
        prop, lat, lon, amen = _share.decode(token)
    except _share.ShareTokenError:
        _metrics.SHARE_EVENTS.labels(event="open", outcome="invalid").inc()
        return _error(request, [_("Invalid or expired share link.")])

    z = zones.zone_for_point(lat, lon)
    if z is None:
        _metrics.SHARE_EVENTS.labels(event="open", outcome="error").inc()
        return _error(request, [_("Address is outside the Milano comune — no OMI zone.")])
    zone_code, zone_name = z

    try:
        quote = omi.lookup(zone_code, prop.property_type, prop.omi_condition)
    except LookupError as e:
        _metrics.SHARE_EVENTS.labels(event="open", outcome="error").inc()
        return _error(request, [_("OMI lookup failed: %(e)s") % {"e": e}])
    quote.zone_descr = zone_name

    ctx = _build_result_context(request, lang, prop, lat, lon, amen, quote, zone_code)
    ctx["is_shared_view"] = True
    _metrics.SHARE_EVENTS.labels(event="open", outcome="ok").inc()
    return _tpl(request, "result.html", ctx)


@app.get("/s/{id}", response_class=HTMLResponse)
def share_view_short(
    request: Request,
    id: str = FPath(min_length=1),
) -> HTMLResponse:
    """Lang-free short share route (D4).  Negotiate locale: cookie → Accept-Language → it_IT."""
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG.get(locale, "it")
    _set_locale(request, lang)

    try:
        prop, lat, lon, amen = _share.resolve(id, _share_store)
        # Determine which path resolve() took without a second store read:
        # short+alnum ids that exist in the store go through the store path;
        # everything else is the legacy decode path.
        _is_short_alnum = len(id) <= 10 and id.isalnum()
        _resolve_path = "store" if _is_short_alnum else "legacy"
    except _share.ShareTokenError:
        _metrics.SHARE_EVENTS.labels(event="open", outcome="invalid").inc()
        _metrics.SHARE_RESOLVE.labels(path="miss").inc()
        return _error(request, [_("Invalid or expired share link.")])

    _metrics.SHARE_RESOLVE.labels(path=_resolve_path).inc()

    z = zones.zone_for_point(lat, lon)
    if z is None:
        _metrics.SHARE_EVENTS.labels(event="open", outcome="error").inc()
        return _error(request, [_("Address is outside the Milano comune — no OMI zone.")])
    zone_code, zone_name = z

    try:
        quote = omi.lookup(zone_code, prop.property_type, prop.omi_condition)
    except LookupError as e:
        _metrics.SHARE_EVENTS.labels(event="open", outcome="error").inc()
        return _error(request, [_("OMI lookup failed: %(e)s") % {"e": e}])
    quote.zone_descr = zone_name

    ctx = _build_result_context(request, lang, prop, lat, lon, amen, quote, zone_code)
    ctx["is_shared_view"] = True
    _metrics.SHARE_EVENTS.labels(event="open", outcome="ok").inc()
    return _tpl(request, "result.html", ctx)


@app.get("/og/{token}.png")
def og_image(token: str) -> Response:
    """Render a 1200×630 branded OG image.

    Accepts both short store ids and legacy long tokens (dual-path via resolve, D5).
    """
    try:
        prop, _lat, _lon, amen = _share.resolve(token, _share_store)
        _is_short_alnum = len(token) <= 10 and token.isalnum()
        _resolve_path = "store" if _is_short_alnum else "legacy"
    except _share.ShareTokenError as exc:
        _metrics.SHARE_EVENTS.labels(event="og_render", outcome="invalid").inc()
        _metrics.SHARE_RESOLVE.labels(path="miss").inc()
        raise HTTPException(status_code=404, detail="Invalid share token") from exc

    _metrics.SHARE_RESOLVE.labels(path=_resolve_path).inc()

    # Recompute estimate for the image (zone + OMI lookup — cheap, bundled data)
    z = zones.zone_for_point(_lat, _lon)
    if z is None:
        _metrics.SHARE_EVENTS.labels(event="og_render", outcome="error").inc()
        raise HTTPException(status_code=404, detail="Address outside Milano comune")
    zone_code, zone_name = z

    try:
        quote = omi.lookup(zone_code, prop.property_type, prop.omi_condition)
    except LookupError as exc:
        _metrics.SHARE_EVENTS.labels(event="og_render", outcome="error").inc()
        raise HTTPException(status_code=404, detail="OMI lookup failed") from exc
    quote.zone_descr = zone_name

    est = engine.estimate(prop, quote, amen)

    png_bytes = _ogimage.render(est, prop)
    _metrics.SHARE_EVENTS.labels(event="og_render", outcome="ok").inc()
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@app.get("/{lang}/api/amenities")
def api_amenities(
    request: Request,
    lang: str = FPath(pattern=_LANG_RE),
    lat: float = 0.0,
    lon: float = 0.0,
) -> Response:
    _set_locale(request, lang)
    try:
        score = amenities.fetch_amenities(lat, lon)
    except amenities.AmenityFetchError as e:
        return Response(
            content=json.dumps({"status": "failed", "error": str(e), "attempts": e.attempts}),
            media_type="application/json",
        )
    except Exception as e:
        return Response(
            content=json.dumps({"status": "failed", "error": str(e), "attempts": 1}),
            media_type="application/json",
        )
    html = templates.env.get_template("_amenity_card.html").render(score=score)
    return Response(
        content=json.dumps({"status": "ok", "score": score.model_dump(), "html": html}),
        media_type="application/json",
    )


@app.api_route("/{path:path}", methods=["GET", "HEAD"])
def bare_path_redirect(request: Request, path: str) -> RedirectResponse:
    # Redirect bare paths (e.g. /about) to the negotiated locale prefix.
    # Paths already carrying a lang prefix that didn't match a real route
    # must 404 here — otherwise we'd loop /it/foo → /it/it/foo → ...
    first = path.split("/", 1)[0]
    if first in SUPPORTED_LANGS:
        raise HTTPException(status_code=404)

    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    qs = request.url.query
    target = f"/{lang}/{path}"
    if qs:
        target += f"?{qs}"
    return RedirectResponse(target, status_code=301)


# ---------------------------------------------------------------------------
# Top-level ASGI dispatcher: exact-match /mcp → MCP, everything else → FastAPI.
#
# MCP Streamable HTTP is a single endpoint (GET for SSE, POST for JSON-RPC) —
# the spec defines no sub-paths under it, so exact-match is sufficient and
# forward-stable. We avoid app.mount("/mcp", ...) because Starlette's Mount
# strips the prefix and 307-redirects bare /mcp to /mcp/, which some MCP
# clients don't follow on POST. Forwarding the scope verbatim sidesteps both.
# ---------------------------------------------------------------------------


async def _dispatch(scope, receive, send):
    if scope.get("type") in ("http", "websocket") and scope.get("path") == "/mcp":
        # Hard-code endpoint name so the metrics middleware labels this branch "mcp".
        scope["endpoint"] = type("_mcp_endpoint", (), {"__name__": "mcp"})()
        await _mcp_asgi_app(scope, receive, send)
        return
    await app(scope, receive, send)


application = _metrics.instrument(_dispatch)


def _error(request: Request, errors: list[str]) -> HTMLResponse:
    # Not routed through _tpl(): callers (import/share/estimate handlers, all in
    # _SEO_MIRRORED_ENDPOINTS) render this for a 400 on their own path, so the
    # route=None fallback in _seo_urls — mirror the current path across languages —
    # is exactly the right, consistent behaviour here too.
    locale = getattr(request.state, "locale", "it_IT")
    lang = LOCALE_TO_LANG.get(locale, "it")
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "lang": lang,
            "locale": locale,
            "errors": errors,
            **_seo_urls(request, lang),
        },
        status_code=400,
    )
