"""Web glue for the purchase-cost calculator and the interest button.

Everything here is presentation: parsing a query string / the result-page form into a
``PurchaseScenario``, listing the select options, and packaging the calculator's output for the
templates. No cost rule lives here — rates are in ``costs/rates.py`` and the arithmetic in
``costs/calculator.py``.

The result-page form and the standalone calculator are plain GET forms (no JavaScript needed):
the scenario travels in the query string, so a result is a bookmarkable, shareable URL.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from stimmo.costs import calculator, rates
from stimmo.i18n import fmt_eur
from stimmo.i18n import gettext as _
from stimmo.models import (
    CADASTRAL_CATEGORY_LABELS,
    RENOVATION_TIER_LABELS,
    SELLER_TYPE_LABELS,
    CadastralCategory,
    CostBreakdown,
    CostGroup,
    Property,
    PurchaseScenario,
    RenovationTier,
    SellerType,
)
from stimmo.web.labels import date_str, month_str, pct_str, tier_short

# Re-exported so web/app.py (sitemap <lastmod>) need not import the rates module directly.
RATES_AS_OF = rates.RATES_AS_OF

# ---------------------------------------------------------------------------
# Interest button
# ---------------------------------------------------------------------------

INTEREST_ENV_VAR = "STIMMO_DOSSIER_INTEREST"
# Price shown on the button copy.
INTEREST_PRICE_EUR = 29
_OFF_VALUES = frozenset({"", "0", "false", "no", "off"})


def interest_enabled() -> bool:
    """Read per call (like the Cloudflare beacon token) so tests can toggle it with monkeypatch.
    Unset means off: nothing renders and the POST endpoint 404s."""
    return os.environ.get(INTEREST_ENV_VAR, "").strip().lower() not in _OFF_VALUES


# ---------------------------------------------------------------------------
# Form <-> scenario
# ---------------------------------------------------------------------------

HOME_PRIMA = "prima"
HOME_OTHER = "other"

# The mortgage field is one number plus this unit: euros, or a percentage of the price (LTV).
MORTGAGE_UNIT_EUR = "eur"
MORTGAGE_UNIT_PCT = "pct"
_MORTGAGE_UNITS = (MORTGAGE_UNIT_EUR, MORTGAGE_UNIT_PCT)

# Values shown on the standalone page before the visitor submits anything: a €450,000 resale
# in Milan, so the page is never an empty form.
EXAMPLE_VALUES: dict[str, str] = {
    "price": "450000",
    "surface": "70",
    "home": HOME_PRIMA,
    "seller": SellerType.PRIVATE.value,
    "category": CadastralCategory.A2.value,
    "rendita": "1100",
    "agency": f"{rates.AGENCY_DEFAULT_PCT:g}",
    "mortgage": "300000",
    "mortgage_unit": MORTGAGE_UNIT_EUR,
    "mortgage_years": str(rates.MORTGAGE_DEFAULT_YEARS),
    "mortgage_rate": f"{rates.MORTGAGE_RATE_DEFAULT_PCT:g}",
    "tier": RenovationTier.MEDIUM.value,
}

_PRICE_MIN_EUR = 10_000.0
_PRICE_MAX_EUR = 100_000_000.0
_SURFACE_MAX_M2 = 10_000.0
_RENDITA_MAX_EUR = 1_000_000.0
_AGENCY_MAX_PCT = 10.0
_RATE_MAX_PCT = 20.0  # same bound as PurchaseScenario.mortgage_rate_pct


@dataclass
class ParsedScenario:
    values: dict[str, str]
    scenario: PurchaseScenario | None = None
    errors: list[str] = field(default_factory=list)


def _g(x: float) -> str:
    """A form-field number: no exponent form (``:g`` would turn 1234567 into 1.23457e+06)
    and no trailing zeros."""
    return f"{x:.4f}".rstrip("0").rstrip(".")


def values_for_property(prop: Property) -> dict[str, str]:
    """Form prefill for a result page: price, surface and the tier suggested by the listing
    condition. The rest are the stated defaults — the form shows them so they can be changed."""
    return {
        "price": _g(prop.asking_price_eur),
        "surface": _g(prop.surface_m2),
        "home": HOME_PRIMA,
        "seller": SellerType.PRIVATE.value,
        "category": CadastralCategory.A2.value,
        "rendita": "",
        "agency": f"{rates.AGENCY_DEFAULT_PCT:g}",
        "mortgage": "",
        "mortgage_unit": MORTGAGE_UNIT_EUR,
        "mortgage_years": str(rates.MORTGAGE_DEFAULT_YEARS),
        "mortgage_rate": f"{rates.MORTGAGE_RATE_DEFAULT_PCT:g}",
        "tier": rates.default_renovation_tier(prop.fine_condition).value,
    }


def _number(raw: str) -> float | None:
    """Parse a plain number; a lone comma is read as the decimal separator. Empty → None."""
    text = raw.strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    value = float(text)  # ValueError propagates to the caller
    if not math.isfinite(value):
        raise ValueError(raw)
    return value


def parse(params: Mapping[str, str], today: date | None = None) -> ParsedScenario:
    """Build a PurchaseScenario from form/query fields; collect translated errors."""
    values = {k: (params.get(k) or "").strip() for k in EXAMPLE_VALUES}
    # Absent selects fall back to their defaults instead of erroring (hand-built links).
    values["home"] = values["home"] or HOME_PRIMA
    values["seller"] = values["seller"] or SellerType.PRIVATE.value
    values["category"] = values["category"] or CadastralCategory.A2.value
    values["tier"] = values["tier"] or RenovationTier.NONE.value
    values["agency"] = values["agency"] or f"{rates.AGENCY_DEFAULT_PCT:g}"
    values["mortgage_unit"] = values["mortgage_unit"] or MORTGAGE_UNIT_EUR
    values["mortgage_years"] = values["mortgage_years"] or str(rates.MORTGAGE_DEFAULT_YEARS)
    # No default for the rate: an absent or empty rate is "not given", never a rate we picked.
    # (The forms pre-fill the reference average, visibly, so the buyer can see and change it.)

    errors: list[str] = []
    nums: dict[str, float | None] = {}
    for name in ("price", "surface", "rendita", "agency", "mortgage", "mortgage_rate"):
        try:
            nums[name] = _number(values[name])
        except ValueError:
            errors.append(_("“%(value)s” is not a valid number.") % {"value": values[name]})
            nums[name] = None

    enums: dict[str, Any] = {}
    for name, enum_cls in (
        ("seller", SellerType),
        ("category", CadastralCategory),
        ("tier", RenovationTier),
    ):
        try:
            enums[name] = enum_cls(values[name])
        except ValueError:
            errors.append(_("Unknown option “%(value)s”.") % {"value": values[name]})
    for name, allowed in (
        ("home", (HOME_PRIMA, HOME_OTHER)),
        ("mortgage_unit", _MORTGAGE_UNITS),
        ("mortgage_years", tuple(str(y) for y in rates.MORTGAGE_YEARS_OPTIONS)),
    ):
        if values[name] not in allowed:
            errors.append(_("Unknown option “%(value)s”.") % {"value": values[name]})

    price = nums["price"]
    if not errors:
        if price is None or not _PRICE_MIN_EUR <= price <= _PRICE_MAX_EUR:
            errors.append(
                _("The purchase price must be between %(low)s and %(high)s.")
                % {"low": fmt_eur(_PRICE_MIN_EUR), "high": fmt_eur(_PRICE_MAX_EUR)}
            )
        agency = nums["agency"]
        if agency is not None and not 0 <= agency <= _AGENCY_MAX_PCT:
            errors.append(
                _("The agency fee must be between %(low)s and %(high)s.")
                % {"low": "0%", "high": f"{_AGENCY_MAX_PCT:g}%"}
            )
        mortgage = nums["mortgage"] or 0.0
        by_ltv = values["mortgage_unit"] == MORTGAGE_UNIT_PCT
        if by_ltv and mortgage > 100:
            errors.append(
                _("The mortgage percentage must be between %(low)s and %(high)s.")
                % {"low": "0%", "high": "100%"}
            )
        elif not by_ltv and price is not None and mortgage > price:
            errors.append(_("The mortgage cannot exceed the purchase price."))
        rate = nums["mortgage_rate"]
        if rate is not None and not 0 <= rate <= _RATE_MAX_PCT:
            errors.append(
                _("The interest rate must be between %(low)s and %(high)s.")
                % {"low": "0%", "high": f"{_RATE_MAX_PCT:g}%"}
            )
        if mortgage < 0 or (nums["surface"] or 0) < 0 or (nums["rendita"] or 0) < 0:
            errors.append(_("Amounts cannot be negative."))
        if (nums["surface"] or 0) > _SURFACE_MAX_M2 or (nums["rendita"] or 0) > _RENDITA_MAX_EUR:
            errors.append(_("One of the values is out of range."))
        surface = nums["surface"] or 0.0
        if enums["tier"] is not RenovationTier.NONE and surface <= 0:
            errors.append(_("Enter the surface to price a renovation."))

    if errors:
        return ParsedScenario(values=values, errors=errors)

    try:
        scenario = PurchaseScenario(
            price_eur=price,
            surface_m2=nums["surface"] or 0.0,
            prima_casa=values["home"] == HOME_PRIMA,
            seller=enums["seller"],
            category=enums["category"],
            rendita_eur=nums["rendita"] or None,
            agency_pct=nums["agency"],
            # Exactly one of the two is set, from the single number and its unit.
            mortgage_eur=0.0 if by_ltv else nums["mortgage"] or 0.0,
            mortgage_ltv_pct=nums["mortgage"] if by_ltv else None,
            mortgage_years=int(values["mortgage_years"]),
            mortgage_rate_pct=nums["mortgage_rate"],
            renovation_tier=enums["tier"],
            as_of=today,
        )
    except ValidationError:  # pragma: no cover - bounds above should make this unreachable
        return ParsedScenario(values=values, errors=[_("One of the values is out of range.")])
    return ParsedScenario(values=values, scenario=scenario)


# ---------------------------------------------------------------------------
# Template context
# ---------------------------------------------------------------------------


def options() -> dict[str, list[tuple[str, str]]]:
    """Select options, resolved under the active locale (call inside a request)."""
    return {
        "homes": [
            (HOME_PRIMA, _("Prima casa — main home, first-home tax regime")),
            (HOME_OTHER, _("Not prima casa — second or investment home")),
        ],
        "sellers": [(e.value, str(SELLER_TYPE_LABELS[e])) for e in SellerType],
        "categories": [(e.value, str(CADASTRAL_CATEGORY_LABELS[e])) for e in CadastralCategory],
        "tiers": [(e.value, str(RENOVATION_TIER_LABELS[e])) for e in RenovationTier],
        "years": [
            (str(y), _("%(years)s years") % {"years": y}) for y in rates.MORTGAGE_YEARS_OPTIONS
        ],
    }


def rate_reference(today: date | None) -> dict[str, Any]:
    """The market reference shown beside the rate field, from costs/rates.py: Banca d'Italia's
    average rates on new house-purchase loans, the month they describe, and whether the review
    is overdue (``today`` None = no clock, nothing is flagged). Call inside a request (labels)."""
    averages = (
        (_("All new loans"), rates.MORTGAGE_RATE_AVERAGE),
        (
            _("Rate set for up to 1 year at the outset (typically variable)"),
            rates.MORTGAGE_RATE_INITIAL_UP_TO_1Y,
        ),
        (
            _("Rate set for over 1 year at the outset (typically fixed)"),
            rates.MORTGAGE_RATE_INITIAL_OVER_1Y,
        ),
    )
    benchmarks = [b for _label, b in averages] + [rates.MORTGAGE_TAEG_AVERAGE]
    return {
        "rows": [(label, pct_str(b.central, 2)) for label, b in averages],
        "taeg": pct_str(rates.MORTGAGE_TAEG_AVERAGE.central, 2),
        "month": month_str(rates.MORTGAGE_RATE_DATA_MONTH),
        "provisional": rates.MORTGAGE_RATE_PROVISIONAL,
        "source_url": rates.MORTGAGE_RATE_SOURCE_URL,
        "stale": today is not None and any(b.overdue(today) for b in benchmarks),
    }


# Where a visitor reports a wrong or missing figure: the GitHub issue form for content corrections
# (.github/ISSUE_TEMPLATE/correzione-contenuti.yml).
REPORT_ISSUE_URL = (
    "https://github.com/alediaferia/stimmo/issues/new?template=correzione-contenuti.yml"
)

# The editorial guide the Italian calculator and result page link to (see web/app.py's
# costs_guide_link): linked only once the guide's file exists in the content directory.
COSTS_GUIDE_SLUG = "spese-nascoste-acquisto-casa-milano"

# Proper-noun titles of the cited sources: shown as written, never translated.
SOURCES: tuple[tuple[str, str], ...] = (
    (
        "Agenzia delle Entrate — Guida per l'acquisto della casa (ottobre 2025)",
        rates.AGENZIA_ENTRATE_GUIDE_URL,
    ),
    ("Studio Madera — costo del notaio", rates.NOTARY_PURCHASE_BANDS[0].benchmark.source_url or ""),
    (
        "Notaionline — costo del notaio per il mutuo",
        rates.NOTARY_MORTGAGE_BANDS[0].benchmark.source_url or "",
    ),
    (
        "Facile.it — spese accessorie del mutuo",
        rates.MORTGAGE_INSTRUCTION_FEE.source_url or "",
    ),
    (
        "Normattiva — D.Lgs. 385/1993 (TUB), art. 38 (credito fondiario)",
        rates.TUB_ART_38_URL,
    ),
    (
        "Banca d'Italia — Istruzioni di Vigilanza, Titolo V, cap. 1 (credito fondiario)",
        rates.BANCA_ITALIA_CIRC_229_URL,
    ),
    ("Consap — Fondo di garanzia per la prima casa", rates.CONSAP_FUND_URL),
    (
        "Normattiva — D.L. 73/2021, art. 64, comma 3 (garanzia elevata all'80%, limiti ISEE)",
        rates.CONSAP_DL73_ART64_URL,
    ),
    (
        "Normattiva — D.L. 66/2026 (L. 116/2026), art. 4-bis (categorie con disabilità)",
        rates.CONSAP_DL66_ART4BIS_URL,
    ),
    (
        "Banca d'Italia — Banche e moneta: serie nazionali, Tavola 2.4 (tassi sui nuovi mutui)",
        rates.MORTGAGE_RATE_SOURCE_URL,
    ),
    ("GHLE — quanto costa ristrutturare casa a Milano nel 2026", rates.RENOVATION_SOURCE_URL),
    (
        "Regione Lombardia — Prezzario regionale dei lavori pubblici 2026, Parte 1 "
        "(guida alla lettura: prezzi al netto di IVA)",
        rates.RENOVATION_IVA_CROSSCHECK_URL,
    ),
    (
        "Edotto — ristrutturazioni edilizie 2026 e detrazione",
        rates.BONUS_RISTRUTTURAZIONE[2026].source_url,
    ),
    (
        "Idealista — bonus edilizi 50% e 36%, proroga 2027",
        rates.BONUS_RISTRUTTURAZIONE[2027].source_url,
    ),
)


def assumptions(s: PurchaseScenario) -> list[str]:
    """The inputs behind a result, as short translated phrases (shown above the totals)."""
    out = [
        _("prima casa") if s.prima_casa else _("not prima casa"),
        str(SELLER_TYPE_LABELS[s.seller]),
        _("category %(category)s") % {"category": s.category.value},
    ]
    out.append(
        _("rendita %(value)s") % {"value": fmt_eur(s.rendita_eur)}
        if s.rendita_eur is not None
        else _("rendita unknown")
    )
    pct = rates.AGENCY_DEFAULT_PCT if s.agency_pct is None else s.agency_pct
    out.append(_("agency %(pct)s + IVA") % {"pct": pct_str(pct)} if pct else _("no agency fee"))
    amount = s.mortgage_amount_eur
    out.append(
        _("mortgage %(amount)s (%(pct)s of the price)")
        % {"amount": fmt_eur(amount), "pct": pct_str(amount / s.price_eur * 100)}
        if amount
        else _("no mortgage")
    )
    if amount and s.mortgage_rate_pct is not None:
        out.append(
            _("%(years)s-year term at %(rate)s TAN")
            % {
                "years": s.mortgage_years or rates.MORTGAGE_DEFAULT_YEARS,
                "rate": pct_str(s.mortgage_rate_pct),
            }
        )
    out.append(
        _("%(tier)s renovation") % {"tier": tier_short(s.renovation_tier)}
        if s.renovation_tier is not RenovationTier.NONE
        else _("no renovation")
    )
    return out


def _cb_context(cb: CostBreakdown) -> dict[str, Any]:
    return {
        "closing_lines": cb.lines_in(CostGroup.CLOSING),
        "renovation_lines": cb.lines_in(CostGroup.RENOVATION),
        "offset_lines": cb.lines_in(CostGroup.OFFSET),
    }


def ltv_slider() -> dict[str, str]:
    """What the mortgage slider (static/costs-form.js) draws and enforces, straight from
    costs/rates.py: the ordinary mutuo fondiario limit, the ceiling reachable with integrative
    guarantees, and the share of the track where the caution zone starts. The numbers are plain
    strings for HTML attributes and CSS; the labels carry the locale's percentage format."""
    limit = rates.MUTUO_FONDIARIO_MAX_LTV_PCT
    ceiling = rates.MUTUO_FONDIARIO_MAX_LTV_WITH_GUARANTEES_PCT
    return {
        "limit": _g(limit),
        "max": _g(ceiling),
        "frac": _g(limit / ceiling),
        "limit_label": pct_str(limit),
        "max_label": pct_str(ceiling),
    }


def build_context(parsed: ParsedScenario, today: date | None = None) -> dict[str, Any]:
    """Everything the costs partials need. ``cb`` is None when the input did not validate.
    ``today`` is only used to flag the rate reference when its review is overdue."""
    ctx: dict[str, Any] = {
        "form": parsed.values,
        "errors": parsed.errors,
        "options": options(),
        "rate_ref": rate_reference(today),
        "ltv_slider": ltv_slider(),
        "sources": SOURCES,
        "report_issue_url": REPORT_ISSUE_URL,
        "rates_as_of": rates.RATES_AS_OF,
        "agency_default_pct": rates.AGENCY_DEFAULT_PCT,
        "cb": None,
    }
    if parsed.scenario is not None:
        cb = calculator.compute(parsed.scenario)
        ctx.update(
            cb=cb,
            scenario=parsed.scenario,
            assumptions=assumptions(parsed.scenario),
            **_cb_context(cb),
        )
    return ctx


def methodology() -> dict[str, Any]:
    """Rates and tables for the calculator page's methodology section, straight from
    costs/rates.py so the prose can never drift from the numbers the calculator applies."""
    frac = lambda x: pct_str(x * 100)  # noqa: E731 - 0.0025 -> "0.25%"
    return {
        "method": {
            "registro_prima": frac(rates.REGISTRO_RATE_PRIMA_CASA),
            "registro_other": frac(rates.REGISTRO_RATE_OTHER),
            "registro_min": fmt_eur(rates.REGISTRO_MIN_EUR),
            "cadastral_prima": (
                f"{rates.CADASTRAL_REVALUATION:g} × {rates.CADASTRAL_MULTIPLIER_PRIMA_CASA}"
            ),
            "cadastral_other": (
                f"{rates.CADASTRAL_REVALUATION:g} × {rates.CADASTRAL_MULTIPLIER_OTHER}"
            ),
            "ipo_cat": f"{fmt_eur(rates.IPOTECARIA_EUR)} + {fmt_eur(rates.CATASTALE_EUR)}",
            "builder_iva": " / ".join(
                frac(x)
                for x in (
                    rates.BUILDER_IVA_PRIMA_CASA,
                    rates.BUILDER_IVA_OTHER,
                    rates.BUILDER_IVA_EXCLUDED_CATEGORY,
                )
            ),
            "builder_fixed": fmt_eur(rates.BUILDER_REGISTRO_EUR),
            "sostitutiva": " / ".join(
                frac(x)
                for x in (
                    rates.IMPOSTA_SOSTITUTIVA_PRIMA_CASA,
                    rates.IMPOSTA_SOSTITUTIVA_OTHER,
                )
            ),
            "renovation_iva": frac(rates.RENOVATION_IVA),
            "bonus_cap": fmt_eur(rates.BONUS_SPEND_CAP_EUR),
            "bonus_years": rates.BONUS_INSTALMENT_YEARS,
            "agency_range": f"{pct_str(rates.AGENCY_LOW_PCT)}–{pct_str(rates.AGENCY_DEFAULT_PCT)}",
        },
        "ltv": {
            "limit": pct_str(rates.MUTUO_FONDIARIO_MAX_LTV_PCT),
            "max": pct_str(rates.MUTUO_FONDIARIO_MAX_LTV_WITH_GUARANTEES_PCT),
            "consap_pct": pct_str(rates.CONSAP_GUARANTEE_PCT),
            "consap_high": pct_str(rates.CONSAP_ENHANCED_GUARANTEE_PCT),
            "consap_max_loan": fmt_eur(rates.CONSAP_MAX_LOAN_EUR),
            "consap_isee": fmt_eur(rates.CONSAP_ENHANCED_ISEE_EUR),
            "consap_disability_isee": fmt_eur(rates.CONSAP_DISABILITY_ENHANCED_ISEE_EUR),
            "consap_disability_from": date_str(rates.CONSAP_DISABILITY_CATEGORIES_FROM),
            # Access limits for large families (3 / 4 / 5+ children under 21), not guarantee ones.
            "consap_family_isee": [
                fmt_eur(isee) for _children, isee in rates.CONSAP_LARGE_FAMILY_ACCESS_ISEE_EUR
            ],
            "consap_ltv": pct_str(rates.CONSAP_ENHANCED_MIN_LTV_PCT),
            "consap_until": date_str(rates.CONSAP_VALID_UNTIL),
        },
        "renovation_tiers": [
            (str(RENOVATION_TIER_LABELS[t]), r.low_eur_m2, r.high_eur_m2, r.open_ended)
            for t, r in rates.RENOVATION_TIERS.items()
        ],
        "bonus_rules": [
            (year, pct_str(r.main_residence_pct), pct_str(r.other_pct), r.provisional)
            for year, r in sorted(rates.BONUS_RISTRUTTURAZIONE.items())
        ],
    }


def result_page_context(prop: Property, calculator_url: str, today: date) -> dict[str, Any]:
    """The costs section of a result page: the property's own scenario with stated defaults."""
    ctx = build_context(parse(values_for_property(prop), today), today)
    ctx["calculator_url"] = calculator_url
    return ctx
