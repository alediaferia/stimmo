from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from stimmo.data import amenities as amenities_mod
from stimmo.data import geocode as geocode_mod
from stimmo.data import history as history_mod
from stimmo.data import omi as omi_mod
from stimmo.data import zones as zones_mod
from stimmo.i18n import LANG_TO_LOCALE, use_locale
from stimmo.models import (
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

from .cache import InMemoryCache

_geocode_cache = InMemoryCache(max_entries=5000)
_amenity_cache = InMemoryCache(max_entries=5000)

_GEOCODE_TTL = 7 * 24 * 3600
_AMENITY_TTL = 24 * 3600


class ToolError(Exception):
    """Produces isError:true with a structured {code, message} payload."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(json.dumps({"code": code, "message": message}))
        self.code = code


def _geocode_cached(address: str) -> tuple[float, float] | None:
    key = address.lower().strip()
    hit = _geocode_cache.get(key)
    if hit is not None:
        return hit
    try:
        result = geocode_mod.geocode(address)
    except LookupError:
        return None
    _geocode_cache.set(key, result, ttl=_GEOCODE_TTL)
    return result


def _amenity_cached(lat: float, lon: float) -> AmenityScore:
    key = f"{lat:.4f},{lon:.4f}"
    hit = _amenity_cache.get(key)
    if hit is not None:
        return hit
    result = amenities_mod.fetch_amenities(lat, lon)
    _amenity_cache.set(key, result, ttl=_AMENITY_TTL)
    return result


def _resolve_zone(address: str, coords: tuple[float, float]) -> tuple[str, str]:
    lat, lon = coords
    zone = zones_mod.zone_for_point(lat, lon)
    if zone is None:
        raise ToolError("outside_milan", f"Address '{address}' is outside the Milano comune.")
    return zone


def _resolve_omi(zone_code: str, property_type: PropertyType, omi_condition: OmiCondition) -> Any:
    available = omi_mod.available_conditions(zone_code, property_type)
    if available and omi_condition not in available:
        raise ToolError(
            "no_omi_quote",
            f"No OMI data for zone {zone_code} / {property_type} / {omi_condition}. "
            f"Available: {[c.value for c in available]}",
        )
    try:
        return omi_mod.lookup(zone_code, property_type, omi_condition)
    except LookupError as exc:
        raise ToolError("no_omi_quote", str(exc)) from exc


# ---------------------------------------------------------------------------
# Tool implementations (registered in server.py)
# ---------------------------------------------------------------------------


async def estimate_property(
    address: str,
    surface_m2: float,
    property_type: PropertyType,
    fine_condition: FineCondition,
    floor: int,
    total_floors: int,
    has_lift: bool,
    asking_price_eur: float,
    construction_era: ConstructionEra,
    energy_class: EnergyClass | None = None,
    outdoor: Outdoor = Outdoor.NONE,
    has_box: bool = False,
    orientation: Orientation = Orientation.MIXED,
    exposure: Exposure = Exposure.STREET,
    has_second_bathroom: bool = False,
    room_count: int | None = None,
    lang: Literal["it", "en"] = "it",
) -> dict[str, Any]:
    locale = LANG_TO_LOCALE.get(lang, "it_IT")
    with use_locale(locale):
        omi_condition = derive_omi_condition(fine_condition)
        prop = Property(
            address=address,
            surface_m2=surface_m2,
            property_type=property_type,
            omi_condition=omi_condition,
            fine_condition=fine_condition,
            floor=floor,
            total_floors=total_floors,
            has_lift=has_lift,
            asking_price_eur=asking_price_eur,
            construction_era=construction_era,
            energy_class=energy_class,
            outdoor=outdoor,
            has_box=has_box,
            orientation=orientation,
            exposure=exposure,
            has_second_bathroom=has_second_bathroom,
            room_count=room_count if room_count and room_count > 0 else None,
        )

        coords = await asyncio.to_thread(_geocode_cached, address)
        if coords is None:
            raise ToolError("not_found", f"Could not geocode address: '{address}'.")

        lat, lon = coords
        zone_code, zone_descr = _resolve_zone(address, coords)
        quote = _resolve_omi(zone_code, property_type, omi_condition)
        quote.zone_descr = zone_descr

        try:
            amenity = await asyncio.to_thread(_amenity_cached, lat, lon)
        except amenities_mod.AmenityFetchError as exc:
            raise ToolError("upstream_timeout", f"Amenity fetch failed: {exc}") from exc

        result = engine.estimate(prop, quote, amenity)
        return result.model_dump()


async def lookup_omi_quote(
    address: str,
    property_type: PropertyType,
    omi_condition: OmiCondition | None = None,
    fine_condition: FineCondition | None = None,
    lang: Literal["it", "en"] = "it",
) -> dict[str, Any]:
    if omi_condition is None and fine_condition is None:
        raise ToolError("invalid_input", "Provide omi_condition or fine_condition.")
    if omi_condition is None:
        omi_condition = derive_omi_condition(fine_condition)  # type: ignore[arg-type]

    locale = LANG_TO_LOCALE.get(lang, "it_IT")
    with use_locale(locale):
        coords = await asyncio.to_thread(_geocode_cached, address)
        if coords is None:
            raise ToolError("not_found", f"Could not geocode address: '{address}'.")

        lat, lon = coords
        zone_code, zone_descr = _resolve_zone(address, coords)
        quote = _resolve_omi(zone_code, property_type, omi_condition)

        return {
            "omi_quote": quote.model_dump(),
            "zone_code": zone_code,
            "zone_descr": zone_descr,
            "lat": lat,
            "lon": lon,
        }


async def geocode_milan_address(
    address: str,
    lang: Literal["it", "en"] = "it",
) -> dict[str, Any]:
    coords = await asyncio.to_thread(_geocode_cached, address)
    if coords is None:
        raise ToolError("not_found", f"Could not geocode address: '{address}'.")

    lat, lon = coords
    zone = zones_mod.zone_for_point(lat, lon)
    if zone is None:
        raise ToolError("outside_milan", f"Address '{address}' is outside the Milano comune.")

    return {"lat": lat, "lon": lon, "normalized_address": address}


async def omi_zone_for_point(
    lat: float,
    lon: float,
    lang: Literal["it", "en"] = "it",
) -> dict[str, Any]:
    zone = zones_mod.zone_for_point(lat, lon)
    if zone is None:
        raise ToolError("outside_milan", f"Point ({lat}, {lon}) is outside the Milano comune.")
    zone_code, zone_descr = zone
    return {"zone_code": zone_code, "zone_descr": zone_descr}


async def amenity_score(
    lat: float,
    lon: float,
    lang: Literal["it", "en"] = "it",
) -> dict[str, Any]:
    try:
        score = await asyncio.to_thread(_amenity_cached, lat, lon)
    except amenities_mod.AmenityFetchError as exc:
        raise ToolError("upstream_timeout", f"Amenity fetch failed: {exc}") from exc
    return score.model_dump()


async def omi_history(
    zone_code: str,
    property_type: PropertyType,
    condition: OmiCondition = OmiCondition.NORMALE,
    lang: Literal["it", "en"] = "it",
) -> list[dict[str, Any]]:
    points = history_mod.series(zone_code, property_type, condition)
    return [
        {"semester": p.semester, "eur_m2_min": p.eur_m2_min, "eur_m2_max": p.eur_m2_max}
        for p in points
    ]
