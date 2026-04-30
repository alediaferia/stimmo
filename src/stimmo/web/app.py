from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from stimmo.data import amenities, geocode, history, ntn, omi, zones
from stimmo.data.importers import immobiliare
from stimmo.models import (
    CONSTRUCTION_ERA_LABELS,
    ORIENTATION_LABELS,
    PROPERTY_TYPE_HINTS,
    AmenityScore,
    ConstructionEra,
    EnergyClass,
    FineCondition,
    OmiCondition,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
)
from stimmo.valuation import engine

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _eur(n: float) -> str:
    return f"€ {n:,.0f}".replace(",", ".")


templates.env.filters["eur"] = _eur


def _semester_months_old(semester: str) -> int:
    """Approximate months elapsed since the semester's start date."""
    try:
        year_str, half_str = semester.split("-")
        sem_start = date(int(year_str), 1 if half_str == "1" else 7, 1)
        today = date.today()
        return max(0, (today.year - sem_start.year) * 12 + (today.month - sem_start.month))
    except (ValueError, AttributeError):
        return 0


_IMPORT_REGISTRY = {"immobiliare": immobiliare.parse}

VERDICT_STYLE = {
    "under": (
        "UNDER-PRICED",
        "warn",
        "In Milano genuinely under-priced listings are rare. Common reasons: "
        "pending inheritance dispute (contenzioso ereditario), building irregularity "
        "(abuso edilizio), easement (servitù), or tenant in situ (affitto in corso). "
        "Verify carefully before proceeding.",
    ),
    "fair": ("FAIR", "ok", "Asking price sits inside the estimated market range."),
    "over": ("OVER-PRICED", "bad", "Asking price is above the estimated market range."),
}


app = FastAPI(title="stimmo — Milan fair-price estimator")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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


def _form_context(
    request: Request,
    defaults_override: dict | None = None,
    import_source: str | None = None,
) -> dict:
    """Build template context for form.html with optional prefill."""
    defaults = {
        "property_type": PropertyType.CIVILI.value,
        "omi_condition": OmiCondition.NORMALE.value,
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
    }
    if defaults_override:
        defaults.update({k: v for k, v in defaults_override.items() if v is not None})

    return {
        "property_types": [
            {"value": e.value, "hint": PROPERTY_TYPE_HINTS[e]} for e in PropertyType
        ],
        "omi_conditions": [e.value for e in OmiCondition],
        "fine_conditions": [e.value for e in FineCondition],
        "energy_classes": ["", *[e.value for e in EnergyClass]],
        "outdoors": [e.value for e in Outdoor],
        "construction_eras": [
            {"value": e.value, "label": CONSTRUCTION_ERA_LABELS[e]} for e in ConstructionEra
        ],
        "orientations": [{"value": e.value, "label": ORIENTATION_LABELS[e]} for e in Orientation],
        "defaults": defaults,
        "import_source": import_source,
    }


@app.get("/about", response_class=HTMLResponse)
def about(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "about.html",
        {
            "semester": omi.SEMESTER,
            "zone_count": len(omi.available_zones()),
        },
    )


@app.get("/", response_class=HTMLResponse)
def form(request: Request) -> HTMLResponse:
    ctx = _form_context(request)
    return templates.TemplateResponse(request, "form.html", ctx)


def _find_listing(node: dict | list | None, depth: int = 0) -> dict | None:
    """Walk __NEXT_DATA__ tree looking for listing leaf object."""
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


@app.get("/import", response_class=HTMLResponse)
def import_get(request: Request, src: str = "", v: str = "1", p: str = "") -> HTMLResponse:
    if src not in _IMPORT_REGISTRY or not p:
        return _error(request, ["Invalid import request"])

    try:
        padded = p + "=" * (-len(p) % 4)
        raw = base64.urlsafe_b64decode(padded)
        if len(raw) > 32_768:
            return _error(request, ["Payload too large"])
        payload = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return _error(request, ["Malformed import payload"])

    prefill = _IMPORT_REGISTRY[src](payload)
    ctx = _form_context(request, prefill, import_source=src)
    return templates.TemplateResponse(request, "form.html", ctx)


@app.post("/import", response_class=HTMLResponse)
def import_post(
    request: Request, src: str = Form("immobiliare"), html: str = Form("")
) -> HTMLResponse:
    if src not in _IMPORT_REGISTRY or not html:
        return _error(request, ["Missing source or HTML content"])

    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return _error(request, ["Could not find listing data in pasted HTML"])

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return _error(request, ["Could not parse listing data"])

    listing = _find_listing(data)
    if not listing:
        return _error(request, ["Could not locate listing fields in pasted data"])

    prefill = _IMPORT_REGISTRY[src](listing)
    ctx = _form_context(request, prefill, import_source=src)
    return templates.TemplateResponse(request, "form.html", ctx)


@app.get("/bookmarklet", response_class=HTMLResponse)
def bookmarklet_page(request: Request) -> HTMLResponse:
    js_path = STATIC_DIR / "bookmarklet.js"
    if not js_path.exists():
        return _error(request, ["Bookmarklet not available"])

    js_src = js_path.read_text()
    stimmo_base = str(request.base_url).rstrip("/")
    js_src = js_src.replace("'https://stimmo.it'", f"'{stimmo_base}'")

    # Collapse whitespace but preserve string literals (don't minify //)
    bookmarklet_href = "javascript:" + re.sub(r"\s+", " ", js_src).strip()

    return templates.TemplateResponse(
        request,
        "bookmarklet.html",
        {"bookmarklet_href": bookmarklet_href},
    )


@app.post("/estimate", response_class=HTMLResponse)
def estimate(
    request: Request,
    address: str = Form(...),
    surface_m2: float = Form(...),
    property_type: str = Form(...),
    omi_condition: str = Form(...),
    fine_condition: str = Form(...),
    floor: int = Form(...),
    total_floors: int = Form(...),
    has_lift: str = Form("off"),
    energy_class: str = Form(""),
    outdoor: str = Form(Outdoor.NONE.value),
    has_box: str = Form("off"),
    construction_era: str = Form(ConstructionEra.POSTWAR_BOOM.value),
    orientation: str = Form(Orientation.MIXED.value),
    has_second_bathroom: str = Form("off"),
    asking_price_eur: float = Form(...),
) -> HTMLResponse:
    errors: list[str] = []
    try:
        prop = Property(
            address=address.strip(),
            surface_m2=surface_m2,
            property_type=PropertyType(property_type),
            omi_condition=OmiCondition(omi_condition),
            fine_condition=FineCondition(fine_condition),
            floor=floor,
            total_floors=total_floors,
            has_lift=has_lift == "on",
            energy_class=EnergyClass(energy_class) if energy_class else None,
            outdoor=Outdoor(outdoor),
            has_box=has_box == "on",
            construction_era=ConstructionEra(construction_era),
            orientation=Orientation(orientation),
            has_second_bathroom=has_second_bathroom == "on",
            asking_price_eur=asking_price_eur,
        )
    except (ValidationError, ValueError) as e:
        errors.append(str(e))
        return _error(request, errors)

    try:
        lat, lon = geocode.geocode(prop.address)
    except LookupError as e:
        return _error(request, [f"Geocoding failed: {e}"])

    z = zones.zone_for_point(lat, lon)
    if z is None:
        return _error(request, ["Address is outside the Milano comune — no OMI zone."])
    zone_code, zone_name = z

    try:
        quote = omi.lookup(zone_code, prop.property_type, prop.omi_condition)
    except LookupError as e:
        return _error(request, [f"OMI lookup failed: {e}"])
    quote.zone_descr = zone_name

    amenity_warning: str | None = None
    try:
        amen = amenities.fetch_amenities(lat, lon)
    except Exception as e:
        amenity_warning = f"Amenity query failed: {e}; using zero score"
        amen = AmenityScore()

    est = engine.estimate(prop, quote, amen)
    series = history.series(zone_code, prop.property_type, prop.omi_condition)
    ntn_total = ntn.total_quarters(last_n=8)
    bucket_label, ntn_bucket = ntn.by_bucket_quarters(prop.surface_m2, last_n=8)

    label, style, hint = VERDICT_STYLE[est.verdict]
    bucket_by_q = {p.quarter: p.ntn for p in ntn_bucket}

    return templates.TemplateResponse(
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
            "verdict_label": label,
            "verdict_style": style,
            "verdict_hint": hint,
            "amenity_warning": amenity_warning,
            "semester_months_old": _semester_months_old(est.omi_quote.semester),
        },
    )


def _error(request: Request, errors: list[str]) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"errors": errors},
        status_code=400,
    )
