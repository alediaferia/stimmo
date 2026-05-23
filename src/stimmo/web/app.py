from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
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

from stimmo.data import amenities, geocode, history, ntn, omi, zones
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
    Orientation,
    Outdoor,
    Property,
    PropertyType,
    derive_omi_condition,
)
from stimmo.valuation import engine
from stimmo.valuation.verdict import HIGH_TOL
from stimmo.web import labels as _labels

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


def _fmt_num(n: float) -> str:
    from babel.numbers import format_decimal

    return format_decimal(round(n), format="#,##0", locale=_current_locale.get())


def _semester_months_old(semester: str) -> int:
    try:
        year_str, half_str = semester.split("-")
        sem_start = date(int(year_str), 1 if half_str == "1" else 7, 1)
        today = date.today()
        return max(0, (today.year - sem_start.year) * 12 + (today.month - sem_start.month))
    except (ValueError, AttributeError):
        return 0


def _set_locale(request: Request, lang: str) -> str:
    """Resolve lang slug → locale, store on request.state and ContextVar."""
    locale = LANG_TO_LOCALE[lang]
    request.state.locale = locale
    _current_locale.set(locale)
    return locale


def _tpl(request: Request, template: str, ctx: dict | None = None) -> HTMLResponse:
    """Render a template with locale context merged in."""
    locale = getattr(request.state, "locale", "it_IT")
    lang = LOCALE_TO_LANG.get(locale, "it")
    base: dict = {"lang": lang, "locale": locale}
    if ctx:
        base.update(ctx)
    return templates.TemplateResponse(request, template, base)


_IMPORT_REGISTRY = {"immobiliare": immobiliare.parse}

app = FastAPI(title="stimmo — Milan fair-price estimator")

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
# Locale negotiation for entry-point redirects
# ---------------------------------------------------------------------------


@app.get("/")
def root_redirect(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    return RedirectResponse(f"/{lang}/", status_code=302)


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


@app.get("/about")
def about_redirect(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    return RedirectResponse(f"/{lang}/about", status_code=302)


@app.get("/bookmarklet")
def bookmarklet_redirect(request: Request) -> RedirectResponse:
    cookie = request.cookies.get("stimmo_lang")
    accept_lang = request.headers.get("Accept-Language")
    locale = negotiate_locale(cookie, accept_lang)
    lang = LOCALE_TO_LANG[locale]
    return RedirectResponse(f"/{lang}/bookmarklet", status_code=302)


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
    )


@app.get("/{lang}/", response_class=HTMLResponse)
def form(request: Request, lang: str = FPath(pattern=_LANG_RE)) -> HTMLResponse:
    _set_locale(request, lang)
    ctx = _form_context(request)
    return _tpl(request, "form.html", ctx)


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
    return _tpl(request, "bookmarklet.html", {"bookmarklet_href": bookmarklet_href})


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

    return _tpl(
        request,
        "result.html",
        {
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
        },
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


@app.get("/{path:path}")
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
    return RedirectResponse(target, status_code=302)


def _error(request: Request, errors: list[str]) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "lang": LOCALE_TO_LANG.get(getattr(request.state, "locale", "it_IT"), "it"),
            "locale": getattr(request.state, "locale", "it_IT"),
            "errors": errors,
        },
        status_code=400,
    )
