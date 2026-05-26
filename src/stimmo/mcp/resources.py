from __future__ import annotations

import json
from typing import Any

from stimmo.data import omi as omi_mod
from stimmo.data.zones import list_zones
from stimmo.models import (
    CONSTRUCTION_ERA_LABELS,
    EXPOSURE_LABELS,
    ORIENTATION_LABELS,
    OUTDOOR_LABELS,
    PROPERTY_TYPE_HINTS,
    ConstructionEra,
    EnergyClass,
    Exposure,
    FineCondition,
    Orientation,
    Outdoor,
    PropertyType,
)

_FINE_CONDITION_LABELS: dict[FineCondition, str] = {
    FineCondition.NUOVO: "New / as-new",
    FineCondition.RISTRUTTURATO: "Renovated",
    FineCondition.ABITABILE: "Habitable / good condition",
    FineCondition.DA_RISTRUTTURARE: "Needs full renovation",
}


def semester_resource() -> str:
    return omi_mod.semester()


def property_type_vocab() -> str:
    items: list[dict[str, Any]] = [
        {"value": pt.value, "hint": str(PROPERTY_TYPE_HINTS[pt])}
        for pt in PropertyType
    ]
    return json.dumps(items, ensure_ascii=False)


def fine_condition_vocab() -> str:
    items = [
        {"value": fc.value, "label": _FINE_CONDITION_LABELS[fc]}
        for fc in FineCondition
    ]
    return json.dumps(items, ensure_ascii=False)


def energy_class_vocab() -> str:
    items = [{"value": ec.value} for ec in EnergyClass]
    return json.dumps(items, ensure_ascii=False)


def outdoor_vocab() -> str:
    items = [{"value": o.value, "label": str(OUTDOOR_LABELS[o])} for o in Outdoor]
    return json.dumps(items, ensure_ascii=False)


def construction_era_vocab() -> str:
    items = [
        {"value": e.value, "label": str(CONSTRUCTION_ERA_LABELS[e])}
        for e in ConstructionEra
    ]
    return json.dumps(items, ensure_ascii=False)


def orientation_vocab() -> str:
    items = [
        {"value": o.value, "label": str(ORIENTATION_LABELS[o])}
        for o in Orientation
    ]
    return json.dumps(items, ensure_ascii=False)


def exposure_vocab() -> str:
    items = [{"value": e.value, "label": str(EXPOSURE_LABELS[e])} for e in Exposure]
    return json.dumps(items, ensure_ascii=False)


def omi_zones_resource() -> str:
    zones = [{"code": code, "descr": descr} for code, descr in list_zones()]
    return json.dumps(zones, ensure_ascii=False)
