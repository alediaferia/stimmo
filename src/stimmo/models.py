from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, NonNegativeFloat, PositiveFloat, PositiveInt


class PropertyType(StrEnum):
    SIGNORILI = "Abitazioni signorili"
    CIVILI = "Abitazioni civili"
    ECONOMICO = "Abitazioni di tipo economico"
    VILLE = "Ville e Villini"


PROPERTY_TYPE_HINTS: dict[PropertyType, str] = {
    PropertyType.SIGNORILI: (
        "Upscale residential — post-2000 build or prestigious older building, doorman (portineria),"
        " marble/parquet finish, prime zone."
        " Pick this if the listing explicitly markets 'signorile'."
    ),
    PropertyType.CIVILI: (
        "Standard residential — typical condominium 1960s–90s, ordinary finishes, no doorman"
        " (most common choice for anything that doesn't clearly fit another category)."
    ),
    PropertyType.ECONOMICO: (
        "Economy residential — palazzina economica, peripheral or semi-peripheral area, basic"
        " finishes, pre-1960s low-spec build or post-war INA-casa type."
    ),
    PropertyType.VILLE: (
        "Detached houses and small villas — independent or semi-detached with private garden/land."
    ),
}


class OmiCondition(StrEnum):
    OTTIMO = "OTTIMO"
    NORMALE = "NORMALE"
    SCADENTE = "SCADENTE"


class FineCondition(StrEnum):
    NUOVO = "nuovo"
    RISTRUTTURATO = "ristrutturato"
    ABITABILE = "abitabile"
    DA_RISTRUTTURARE = "da ristrutturare"


class EnergyClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"


class Outdoor(StrEnum):
    NONE = "none"
    BALCONY = "balcony"
    TERRACE_SMALL = "terrace_small"  # <= 10 m²
    TERRACE_LARGE = "terrace_large"  # > 10 m²


class ConstructionEra(StrEnum):
    PRE_WAR = "pre_war"  # < 1945
    POSTWAR_BOOM = "postwar_boom"  # 1945–1980 (baseline)
    EIGHTIES_90S = "eighties_90s"  # 1981–2000
    CONTEMPORARY = "contemporary"  # 2001–2015
    RECENT = "recent"  # > 2015


CONSTRUCTION_ERA_LABELS: dict[ConstructionEra, str] = {
    ConstructionEra.PRE_WAR: "Pre-war (before 1945)",
    ConstructionEra.POSTWAR_BOOM: "Post-war boom (1945–1980)",
    ConstructionEra.EIGHTIES_90S: "1980s–90s (1981–2000)",
    ConstructionEra.CONTEMPORARY: "Contemporary (2001–2015)",
    ConstructionEra.RECENT: "Recent (after 2015)",
}


class Orientation(StrEnum):
    SOUTH = "south"  # south / south-west
    MIXED = "mixed"  # east, west, split (baseline)
    NORTH = "north"  # predominantly north-facing


ORIENTATION_LABELS: dict[Orientation, str] = {
    Orientation.SOUTH: "South / south-west",
    Orientation.MIXED: "East / west / mixed",
    Orientation.NORTH: "North / north-east",
}


class Property(BaseModel):
    address: str = Field(max_length=200)
    surface_m2: PositiveFloat
    property_type: PropertyType
    omi_condition: OmiCondition
    fine_condition: FineCondition
    floor: int = Field(description="0 = ground/piano terra; -1 = seminterrato")
    total_floors: PositiveInt
    has_lift: bool
    energy_class: EnergyClass | None = None
    outdoor: Outdoor = Outdoor.NONE
    has_box: bool = False
    construction_era: ConstructionEra
    orientation: Orientation
    has_second_bathroom: bool = False
    asking_price_eur: PositiveFloat


class OmiQuote(BaseModel):
    zone_code: str
    zone_descr: str | None = None
    property_type: PropertyType
    condition: OmiCondition
    eur_m2_min: PositiveFloat
    eur_m2_max: PositiveFloat
    semester: str  # e.g. "2018-2"


class AmenityScore(BaseModel):
    metro_within_500m: int = 0
    tram_within_500m: int = 0
    parks_large_within_500m: int = 0
    supermarkets_within_500m: int = 0
    schools_within_500m: int = 0
    pharmacies_within_500m: int = 0
    metro_500_1000m: int = 0
    tram_500_1000m: int = 0
    parks_large_500_1000m: int = 0
    supermarkets_500_1000m: int = 0
    schools_500_1000m: int = 0
    pharmacies_500_1000m: int = 0
    score_pct: NonNegativeFloat = 0.0  # additive %, capped


class AdjustmentBreakdown(BaseModel):
    name: str
    delta_pct: float


Verdict = Literal["under", "fair", "over"]


class Estimate(BaseModel):
    base_eur_m2_min: float
    base_eur_m2_max: float
    multiplier: float
    flat_extras_eur: NonNegativeFloat = 0
    adjusted_eur_m2_min: float
    adjusted_eur_m2_max: float
    adjusted_eur_m2_mid: float
    range_low_eur: float
    range_mid_eur: float
    range_high_eur: float
    ask_premium_pct: float
    ask_range_low_eur: float
    ask_range_mid_eur: float
    ask_range_high_eur: float
    breakdown: list[AdjustmentBreakdown]
    omi_quote: OmiQuote
    amenity_score: AmenityScore
    verdict: Verdict
    asking_vs_ask_mid_pct: float
