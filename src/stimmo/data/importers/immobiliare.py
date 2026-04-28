from __future__ import annotations

import re
from typing import Any

from stimmo.models import (
    ConstructionEra,
    EnergyClass,
    FineCondition,
    OmiCondition,
    Orientation,
    Outdoor,
    PropertyType,
)


def parse(payload: dict) -> dict[str, Any]:
    """Extract stimmo form fields from an immobiliare listing leaf object.

    Returns a partial dict; only keys the parser can confidently infer.
    Missing fields are omitted so form defaults apply.
    """
    result: dict[str, Any] = {}

    # address: combine location.address, streetNumber, city
    try:
        address = payload.get("location", {}).get("address", "").strip()
        street_num = payload.get("location", {}).get("streetNumber", "").strip()
        city = payload.get("location", {}).get("city", "").strip()
        parts = [address, street_num, city]
        full = ", ".join(p for p in parts if p)
        if full:
            result["address"] = full
    except (KeyError, AttributeError, TypeError):
        pass

    # surface_m2 — strip " m²" and handle Italian decimal "153,0 m²"
    try:
        surface = payload.get("surface") or payload.get("surfaceValue", "")
        if surface:
            clean = re.sub(r"[^\d,\.]", "", str(surface)).replace(",", ".")
            m2 = int(float(clean))
            if m2 > 0:
                result["surface_m2"] = m2
    except (ValueError, AttributeError, TypeError):
        pass

    # asking_price_eur — bookmarklet sends price as int (listing.price?.value);
    # raw leaf has price as dict with a "value" key
    try:
        price = payload.get("price")
        if isinstance(price, dict):
            price = price.get("value")
        if price and int(price) > 0:
            result["asking_price_eur"] = int(price)
    except (ValueError, AttributeError, TypeError):
        pass

    # floor + total_floors
    try:
        floor_val = _parse_floor(
            payload.get("floor", {}).get("abbreviation"),
            payload.get("floors"),
        )
        if floor_val is not None:
            result["floor"] = floor_val
        total = _parse_total_floors(payload.get("floors"))
        if total is not None:
            result["total_floors"] = total
    except (ValueError, AttributeError, TypeError):
        pass

    # has_lift
    try:
        if payload.get("elevator") is not None:
            result["has_lift"] = bool(payload.get("elevator"))
    except (AttributeError, TypeError):
        pass

    # energy_class
    try:
        energy = payload.get("energy", {}).get("class", {}).get("name", "").strip()
        if energy:
            clean = re.sub(r"\d+$", "", energy).upper()
            if clean in [e.value for e in EnergyClass]:
                result["energy_class"] = clean
    except (ValueError, AttributeError, TypeError):
        pass

    # property_type
    try:
        search = _build_type_search_string(payload)
        ptype = _classify_property_type(search)
        if ptype:
            result["property_type"] = ptype.value
    except (AttributeError, TypeError):
        pass

    # omi_condition + fine_condition
    try:
        omi, fine = _parse_condition(payload)
        if omi:
            result["omi_condition"] = omi.value
        if fine:
            result["fine_condition"] = fine.value
    except (ValueError, AttributeError, TypeError):
        pass

    # has_second_bathroom
    try:
        bathrooms = payload.get("bathrooms")
        if bathrooms:
            count = int(bathrooms)
            result["has_second_bathroom"] = count >= 2
    except (ValueError, AttributeError, TypeError):
        pass

    # outdoor
    try:
        outdoor = _parse_outdoor(payload)
        if outdoor:
            result["outdoor"] = outdoor.value
    except (ValueError, AttributeError, TypeError):
        pass

    # has_box
    try:
        has_box = _parse_has_box(payload)
        result["has_box"] = has_box
    except (AttributeError, TypeError):
        pass

    # construction_era
    try:
        year = payload.get("buildingYear")
        if year:
            era = _year_to_era(int(year))
            if era:
                result["construction_era"] = era.value
    except (ValueError, AttributeError, TypeError):
        pass

    # orientation
    try:
        orientation = _parse_orientation(payload.get("description", ""))
        if orientation:
            result["orientation"] = orientation.value
    except (AttributeError, TypeError):
        pass

    return result


def _parse_floor(abbrev: str | None, floors_str: str | None) -> int | None:
    """Convert floor abbreviation to numeric floor."""
    if not abbrev:
        return None

    abbrev = abbrev.strip().upper()

    if abbrev == "T":  # terraza
        return 0
    elif abbrev == "S":  # sotterraneo (basement)
        return -1
    elif abbrev == "R":  # rialzato (raised)
        return 0
    elif abbrev == "A":  # attico (attic)
        total = _parse_total_floors(floors_str)
        return total if total else 0

    try:
        return int(abbrev)
    except ValueError:
        return None


def _parse_total_floors(floors_str: str | None) -> int | None:
    """Extract total floors from string like '4 piani'."""
    if not floors_str:
        return None
    m = re.search(r"(\d+)\s*piani", str(floors_str))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _build_type_search_string(payload: dict) -> str:
    """Build lowercase search string from typologyValue, typology.name, category.name."""
    parts = []
    if payload.get("typologyValue"):
        parts.append(str(payload["typologyValue"]))
    if payload.get("typology", {}).get("name"):
        parts.append(str(payload["typology"]["name"]))
    if payload.get("category", {}).get("name"):
        parts.append(str(payload["category"]["name"]))
    return " ".join(parts).lower()


def _classify_property_type(search: str) -> PropertyType | None:
    """Map search string to PropertyType."""
    if any(w in search for w in ["signoril", "lusso", "prestigio"]):
        return PropertyType.SIGNORILI
    if any(w in search for w in ["villa", "villino"]):
        return PropertyType.VILLE
    if any(w in search for w in ["economic", "popolare"]):
        return PropertyType.ECONOMICO
    if search:
        return PropertyType.CIVILI
    return None


def _parse_condition(payload: dict) -> tuple[OmiCondition | None, FineCondition | None]:
    """Extract OMI + fine condition from id (preferred) or string (fallback)."""
    condition_id_map = {
        1: (OmiCondition.OTTIMO, FineCondition.NUOVO),
        3: (OmiCondition.OTTIMO, FineCondition.RISTRUTTURATO),
        2: (OmiCondition.NORMALE, FineCondition.ABITABILE),
        4: (OmiCondition.SCADENTE, FineCondition.DA_RISTRUTTURARE),
    }

    cond_id = payload.get("conditionId")
    if cond_id in condition_id_map:
        return condition_id_map[cond_id]

    cond_str = payload.get("condition", "").lower()
    if any(w in cond_str for w in ["nuovo", "nuova costruzione", "in costruzione"]):
        return OmiCondition.OTTIMO, FineCondition.NUOVO
    if any(w in cond_str for w in ["ottimo", "ristrutturato"]):
        return OmiCondition.OTTIMO, FineCondition.RISTRUTTURATO
    if any(w in cond_str for w in ["buono", "abitabile", "discreto"]):
        return OmiCondition.NORMALE, FineCondition.ABITABILE
    if "da ristrutturare" in cond_str:
        return OmiCondition.SCADENTE, FineCondition.DA_RISTRUTTURARE

    return None, None


def _parse_outdoor(payload: dict) -> Outdoor | None:
    """Build feature set and classify outdoor."""
    features = set()

    # Collect from primaryFeatures (where value==1 or isVisible==true)
    for feat in payload.get("primaryFeatures", []):
        if feat.get("value") == 1 or feat.get("isVisible"):
            code = feat.get("codeName") or feat.get("name")
            if code:
                features.add(str(code).lower())

    # Collect from mainFeatures
    for feat in payload.get("mainFeatures", []):
        ftype = feat.get("type")
        if ftype:
            features.add(str(ftype).lower())

    # Collect from ga4features and features arrays (already lowercased)
    for feat in payload.get("ga4features", []):
        features.add(str(feat).lower())
    for feat in payload.get("features", []):
        features.add(str(feat).lower())

    if not features:
        return None

    if any(w in features for w in ["terrace", "terrazz"]):
        return Outdoor.TERRACE_SMALL
    if any(w in features for w in ["balcony", "balcon"]):
        return Outdoor.BALCONY

    return None


def _parse_has_box(payload: dict) -> bool:
    """Check if property includes box/garage/parking."""
    features = set()

    # Collect from primaryFeatures
    for feat in payload.get("primaryFeatures", []):
        if feat.get("value") == 1 or feat.get("isVisible"):
            code = feat.get("codeName") or feat.get("name")
            if code:
                features.add(str(code).lower())

    # Collect from mainFeatures
    for feat in payload.get("mainFeatures", []):
        ftype = feat.get("type")
        if ftype:
            features.add(str(ftype).lower())

    # Collect from ga4features and features
    for feat in payload.get("ga4features", []):
        features.add(str(feat).lower())
    for feat in payload.get("features", []):
        features.add(str(feat).lower())

    for feat in features:
        if any(w in feat for w in ["garage", "box", "posto auto"]):
            return True
        if any(w in feat for w in ["basement", "cantina"]):
            continue

    return False


def _year_to_era(year: int) -> ConstructionEra | None:
    """Map building year to ConstructionEra."""
    if year < 1945:
        return ConstructionEra.PRE_WAR
    elif year <= 1980:
        return ConstructionEra.POSTWAR_BOOM
    elif year <= 2000:
        return ConstructionEra.EIGHTIES_90S
    elif year <= 2015:
        return ConstructionEra.CONTEMPORARY
    else:
        return ConstructionEra.RECENT


def _parse_orientation(description: str) -> Orientation | None:
    """Scan description for cardinal directions."""
    desc = description.lower() if description else ""
    if any(w in desc for w in ["esposizione sud", "esposto a sud"]):
        return Orientation.SOUTH
    if "nord" in desc:
        return Orientation.NORTH
    return None
