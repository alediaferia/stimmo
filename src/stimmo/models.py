from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    NonNegativeFloat,
    PositiveFloat,
    PositiveInt,
    model_validator,
)

from stimmo.i18n import gettext_lazy as _l


class Exposure(StrEnum):
    STREET = "street"
    MIXED = "mixed"
    INTERNAL_COURTYARD = "internal_courtyard"


EXPOSURE_LABELS: dict[Exposure, Any] = {
    Exposure.STREET: _l("Street-facing"),
    Exposure.MIXED: _l("Mixed (street + courtyard)"),
    Exposure.INTERNAL_COURTYARD: _l("Internal courtyard"),
}


class PropertyType(StrEnum):
    SIGNORILI = "Abitazioni signorili"
    CIVILI = "Abitazioni civili"
    ECONOMICO = "Abitazioni di tipo economico"
    VILLE = "Ville e Villini"


PROPERTY_TYPE_HINTS: dict[PropertyType, Any] = {
    PropertyType.SIGNORILI: _l(
        "Upscale residential — post-2000 build or prestigious older building, doorman (portineria),"
        " marble/parquet finish, prime zone."
        " Pick this if the listing explicitly markets 'signorile'."
    ),
    PropertyType.CIVILI: _l(
        "Standard residential — typical condominium 1960s–90s, ordinary finishes, no doorman"
        " (most common choice for anything that doesn't clearly fit another category)."
    ),
    PropertyType.ECONOMICO: _l(
        "Economy residential — palazzina economica, peripheral or semi-peripheral area, basic"
        " finishes, pre-1960s low-spec build or post-war INA-casa type."
    ),
    PropertyType.VILLE: _l(
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


_FINE_TO_OMI: dict[FineCondition, OmiCondition] = {
    FineCondition.NUOVO: OmiCondition.OTTIMO,
    FineCondition.RISTRUTTURATO: OmiCondition.OTTIMO,
    FineCondition.ABITABILE: OmiCondition.NORMALE,
    FineCondition.DA_RISTRUTTURARE: OmiCondition.SCADENTE,
}


def derive_omi_condition(fine: FineCondition) -> OmiCondition:
    return _FINE_TO_OMI[fine]


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


OUTDOOR_LABELS: dict[Outdoor, Any] = {
    Outdoor.NONE: _l("No outdoor space"),
    Outdoor.BALCONY: _l("Balcony"),
    Outdoor.TERRACE_SMALL: _l("Small terrace (≤ 10 m²)"),
    Outdoor.TERRACE_LARGE: _l("Large terrace (> 10 m²)"),
}


class ConstructionEra(StrEnum):
    PRE_WAR = "pre_war"  # < 1945
    POSTWAR_BOOM = "postwar_boom"  # 1945–1980 (baseline)
    EIGHTIES_90S = "eighties_90s"  # 1981–2000
    CONTEMPORARY = "contemporary"  # 2001–2015
    RECENT = "recent"  # > 2015


CONSTRUCTION_ERA_LABELS: dict[ConstructionEra, Any] = {
    ConstructionEra.PRE_WAR: _l("Pre-war (before 1945)"),
    ConstructionEra.POSTWAR_BOOM: _l("Post-war boom (1945–1980)"),
    ConstructionEra.EIGHTIES_90S: _l("1980s–90s (1981–2000)"),
    ConstructionEra.CONTEMPORARY: _l("Contemporary (2001–2015)"),
    ConstructionEra.RECENT: _l("Recent (after 2015)"),
}


class Orientation(StrEnum):
    SOUTH = "south"  # south / south-west
    MIXED = "mixed"  # east, west, split (baseline)
    NORTH = "north"  # predominantly north-facing


ORIENTATION_LABELS: dict[Orientation, Any] = {
    Orientation.SOUTH: _l("South / south-west"),
    Orientation.MIXED: _l("East / west / mixed"),
    Orientation.NORTH: _l("North / north-east"),
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
    exposure: Exposure = Exposure.STREET
    has_second_bathroom: bool = False
    room_count: PositiveInt | None = None
    asking_price_eur: PositiveFloat


class OmiQuote(BaseModel):
    zone_code: str
    zone_descr: str | None = None
    property_type: PropertyType
    condition: OmiCondition
    eur_m2_min: PositiveFloat
    eur_m2_max: PositiveFloat
    semester: str  # e.g. "2018-2"


class AmenityItem(BaseModel):
    kind: Literal["metro", "tram", "park", "supermarket", "school", "pharmacy"]
    name: str | None = None
    lat: float
    lon: float


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
    items_within_500m: list[AmenityItem] = []


class AdjustmentBreakdown(BaseModel):
    code: str
    params: dict[str, Any] = {}
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


# ---------------------------------------------------------------------------
# Purchase-cost calculator (stimmo.costs)
#
# Frontend-agnostic, like the valuation models above: the calculator emits
# structured lines (`code` + `params`) and web/labels.py renders them for the
# active locale. None of this feeds `Estimate` or `engine.estimate()`.
# ---------------------------------------------------------------------------


class SellerType(StrEnum):
    PRIVATE = "private"  # private individual, or any seller outside the IVA regime
    BUILDER_VAT = "builder_vat"  # builder / developer selling under IVA


SELLER_TYPE_LABELS: dict[SellerType, Any] = {
    SellerType.PRIVATE: _l("Private seller (or not subject to IVA)"),
    SellerType.BUILDER_VAT: _l("Builder selling under IVA"),
}


class CadastralCategory(StrEnum):
    """Residential cadastral categories relevant to the purchase taxes.

    A/1, A/8 and A/9 are excluded from the prima-casa regime and carry the 22 % IVA rate
    on a purchase from a builder.
    """

    A1 = "A/1"
    A2 = "A/2"
    A3 = "A/3"
    A4 = "A/4"
    A5 = "A/5"
    A7 = "A/7"
    A8 = "A/8"
    A9 = "A/9"


CADASTRAL_CATEGORY_LABELS: dict[CadastralCategory, Any] = {
    CadastralCategory.A1: _l("A/1 · Upscale residential (signorile)"),
    CadastralCategory.A2: _l("A/2 · Standard residential (civile)"),
    CadastralCategory.A3: _l("A/3 · Economy residential (economico)"),
    CadastralCategory.A4: _l("A/4 · Working-class residential (popolare)"),
    CadastralCategory.A5: _l("A/5 · Basic residential (ultrapopolare)"),
    CadastralCategory.A7: _l("A/7 · Small villa (villino)"),
    CadastralCategory.A8: _l("A/8 · Villa"),
    CadastralCategory.A9: _l("A/9 · Castle or palace of artistic value"),
}


class RenovationTier(StrEnum):
    NONE = "none"
    LIGHT = "light"
    MEDIUM = "medium"
    FULL = "full"
    PREMIUM = "premium"


RENOVATION_TIER_LABELS: dict[RenovationTier, Any] = {
    RenovationTier.NONE: _l("None — move-in ready"),
    RenovationTier.LIGHT: _l("Light — paint, overlay floors, doors"),
    RenovationTier.MEDIUM: _l("Medium — bathroom, kitchen, electrical, floors"),
    RenovationTier.FULL: _l("Full — strip-out, new layout, systems, windows"),
    RenovationTier.PREMIUM: _l("Premium — high-end or structural"),
}


class CostGroup(StrEnum):
    CLOSING = "closing"  # one-off costs of acquiring the home
    RENOVATION = "renovation"  # works on top of the price
    OFFSET = "offset"  # tax relief — shown separately, never netted from the totals


class CostStatus(StrEnum):
    STATUTORY = "statutory"  # fixed by law given the inputs
    CONTRACTUAL = "contractual"  # set by what the buyer agrees (the agency fee percentage)
    BENCHMARK = "benchmark"  # typical published range
    INDICATIVE = "indicative"  # range not confirmed current or without a public source
    NEEDS_INPUT = "needs_input"  # bounds only until the user supplies a missing input


def mortgage_at_ltv(price_eur: float, ltv_pct: float) -> float:
    """The loan, in whole euros, that is ``ltv_pct`` percent of ``price_eur``.

    The one place a loan-to-value percentage becomes an amount: half-up to the euro (not
    banker's rounding, which would turn 50,000.5 into 50,000), and never above the price.
    The calculator measures the LTV thresholds against this same function, so a percentage
    typed as exactly 80 is never read as "above 80" because of the rounding.
    """
    exact = Decimal(str(price_eur)) * Decimal(str(ltv_pct)) / 100
    return float(min(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP), math.floor(price_eur)))


class PurchaseScenario(BaseModel):
    price_eur: PositiveFloat
    surface_m2: NonNegativeFloat = 0
    prima_casa: bool
    seller: SellerType = SellerType.PRIVATE
    category: CadastralCategory = CadastralCategory.A2
    rendita_eur: PositiveFloat | None = None  # rendita catastale; rarely stated in listings
    agency_pct: float | None = Field(default=None, ge=0, le=10)  # None → rates default
    # The mortgage is given as an amount (mortgage_eur) or as a percentage of the price
    # (mortgage_ltv_pct), never both. 0 in mortgage_eur means "not given", so an LTV may
    # accompany the default; mortgage_amount_eur is the single normalised amount.
    mortgage_eur: NonNegativeFloat = 0
    mortgage_ltv_pct: float | None = Field(default=None, ge=0, le=100)
    renovation_tier: RenovationTier = RenovationTier.NONE
    # Term and nominal annual rate (TAN) of the mortgage, for the monthly payment. Only read when
    # there is a mortgage. None years = the rates.py default term; None rate = not given, and then
    # no payment is computed — the calculator never picks a rate for the buyer.
    mortgage_years: int | None = Field(default=None, ge=1, le=40)
    mortgage_rate_pct: float | None = Field(default=None, ge=0, le=20)
    # Calendar date the figures are read at. Decides which bonus payment years are still
    # shown and which benchmark ranges are past their review date (CostLine.stale); None shows
    # every year in the table and flags nothing, which keeps compute() deterministic.
    as_of: date | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PurchaseScenario:
        if self.renovation_tier is not RenovationTier.NONE and self.surface_m2 <= 0:
            raise ValueError("surface_m2 is required to price a renovation")
        if self.mortgage_ltv_pct is not None and self.mortgage_eur > 0:
            raise ValueError("give the mortgage as mortgage_eur or mortgage_ltv_pct, not both")
        if self.mortgage_eur > self.price_eur:
            raise ValueError("mortgage_eur cannot exceed price_eur")
        return self

    @property
    def mortgage_amount_eur(self) -> float:
        """The mortgage as an amount, whichever way it was given. Everything downstream
        (imposta sostitutiva, notary band, down payment, notes) reads this, never the raw
        fields."""
        if self.mortgage_ltv_pct is not None:
            return mortgage_at_ltv(self.price_eur, self.mortgage_ltv_pct)
        return self.mortgage_eur


class CostRange(BaseModel):
    low_eur: float
    central_eur: float
    high_eur: float


class CostLine(BaseModel):
    code: str
    params: dict[str, Any] = {}
    group: CostGroup
    status: CostStatus
    low_eur: NonNegativeFloat
    central_eur: NonNegativeFloat
    high_eur: NonNegativeFloat
    source_url: str | None = None
    as_of: date | None = None
    # Review date of the benchmark the line is built from (as_of + its review interval); None on
    # lines with no review interval (statutory, or derived from other lines).
    due: date | None = None
    # True when the scenario's date is past `due`: the range may no longer be current. Separate
    # from `status`, because a line can be both indicative and stale. Never set without a clock.
    stale: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> CostLine:
        eps = 1e-6  # float slack: central is computed, not typed
        if self.low_eur > self.central_eur + eps or self.central_eur > self.high_eur + eps:
            raise ValueError(f"{self.code}: expected low <= central <= high")
        return self


class CostNote(BaseModel):
    """A caveat the renderer must surface (e.g. registro bounded because rendita is missing)."""

    code: str
    params: dict[str, Any] = {}
    # Set on notes that state a rule (e.g. the mortgage LTV limits) rather than a calculation.
    source_url: str | None = None


class MortgagePayment(BaseModel):
    """A mortgage's monthly payment under French amortisation: equal monthly instalments of
    principal and interest at a constant rate. A generic calculation on the amount, term and rate
    the scenario gives — nothing here depends on the buyer."""

    years: int
    months: int
    rate_pct: float  # the nominal annual rate (TAN) used, as given
    monthly_payment_eur: float
    total_interest_eur: float
    total_repaid_eur: float  # principal + interest, over the whole term
    # The mortgage-related one-off costs already in the closing costs (imposta sostitutiva,
    # mortgage-deed notary, istruttoria, perizia, insurance); a range like the lines they sum.
    mortgage_costs: CostRange
    # Total cost of the mortgage: interest + mortgage_costs. The purchase price is not in it.
    total_cost: CostRange


class CostBreakdown(BaseModel):
    price_eur: float
    mortgage_eur: float  # the normalised amount, whether the scenario gave euros or an LTV
    mortgage_ltv_pct: float  # mortgage_eur as a percentage of the price (not of the appraisal)
    down_payment_eur: float  # price - mortgage: the first part of cash_needed
    lines: list[CostLine]
    notes: list[CostNote] = []
    closing: CostRange
    renovation: CostRange
    total_on_top: CostRange  # closing + renovation; bonus offsets are NOT subtracted
    # down_payment_eur + total_on_top; bonus offsets NOT subtracted
    cash_needed: CostRange
    # None without a mortgage, or with one but no rate (then a `mortgage_rate_missing` note)
    mortgage_payment: MortgagePayment | None = None
    rates_as_of: date

    def lines_in(self, group: CostGroup) -> list[CostLine]:
        return [ln for ln in self.lines if ln.group is group]
