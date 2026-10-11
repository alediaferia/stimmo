"""Pure purchase-cost calculator: ``compute(scenario) -> CostBreakdown``.

No I/O and no clock. Every rate comes from ``costs/rates.py``; this module only applies them.
Lines carry a structured ``code`` + ``params`` and are rendered to translated text by
``web/labels.py``, the same way ``AdjustmentBreakdown`` is. Nothing here touches the valuation
engine: the estimate, the verdict and ``valuation/adjustments.py`` are not inputs or outputs.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from stimmo.costs import rates
from stimmo.models import (
    CostBreakdown,
    CostGroup,
    CostLine,
    CostNote,
    CostRange,
    CostStatus,
    MortgagePayment,
    PurchaseScenario,
    RenovationTier,
    SellerType,
    mortgage_at_ltv,
)


def _r(x: float) -> float:
    return round(x, 2)


def _pct(fraction: float) -> float:
    """0.0025 -> 0.25 (display percentage, free of binary-float noise)."""
    return round(fraction * 100, 4)


def _line(
    code: str,
    group: CostGroup,
    status: CostStatus,
    low: float,
    central: float,
    high: float,
    *,
    params: dict[str, Any] | None = None,
    source_url: str | None = None,
    as_of: date | None = None,
    due: date | None = None,
    today: date | None = None,
) -> CostLine:
    return CostLine(
        code=code,
        params=params or {},
        group=group,
        status=status,
        low_eur=_r(low),
        central_eur=_r(central),
        high_eur=_r(high),
        source_url=source_url,
        as_of=as_of,
        due=due,
        # Judged against the scenario's date, never a clock: without one nothing is stale.
        stale=due is not None and today is not None and today > due,
    )


def _fixed(
    code: str,
    group: CostGroup,
    status: CostStatus,
    amount: float,
    *,
    params: dict[str, Any] | None = None,
    source_url: str | None = None,
    as_of: date | None = None,
) -> CostLine:
    return _line(
        code,
        group,
        status,
        amount,
        amount,
        amount,
        params=params,
        source_url=source_url,
        as_of=as_of,
    )


def _benchmark_line(
    code: str,
    group: CostGroup,
    b: rates.Benchmark,
    *,
    today: date | None,
    scale: float = 1.0,
    plus: float = 0.0,
    params: dict[str, Any] | None = None,
) -> CostLine:
    """A line straight from a Benchmark: ``(value × scale) + plus`` for low/central/high.

    ``today`` is the scenario's date; the line is stale when it is past the benchmark's ``due``.
    It is required (None is a legitimate "no clock") so a call site cannot forget it.
    """
    status = CostStatus.INDICATIVE if b.indicative else CostStatus.BENCHMARK
    return _line(
        code,
        group,
        status,
        b.low * scale + plus,
        b.central * scale + plus,
        b.high * scale + plus,
        params=params,
        source_url=b.source_url,
        as_of=b.as_of,
        due=b.due,
        today=today,
    )


def _band_for(bands: tuple[rates.PriceBand, ...], amount: float) -> tuple[rates.PriceBand, bool]:
    """The first band covering ``amount``; the top band (flagged) when none does."""
    for band in bands:
        if amount <= band.max_eur:
            return band, False
    return bands[-1], True


# ---------------------------------------------------------------------------
# Closing costs
# ---------------------------------------------------------------------------


def _purchase_taxes(s: PurchaseScenario, prima: bool, notes: list[CostNote]) -> list[CostLine]:
    src = rates.AGENZIA_ENTRATE_GUIDE_URL
    asof = rates.STATUTORY_AS_OF
    G, S = CostGroup.CLOSING, CostStatus

    if s.seller is SellerType.BUILDER_VAT:
        if s.category in rates.PRIMA_CASA_EXCLUDED_CATEGORIES:
            iva_rate = rates.BUILDER_IVA_EXCLUDED_CATEGORY
        elif prima:
            iva_rate = rates.BUILDER_IVA_PRIMA_CASA
        else:
            iva_rate = rates.BUILDER_IVA_OTHER
        return [
            _fixed(
                "vat_builder",
                G,
                S.STATUTORY,
                s.price_eur * iva_rate,
                params={"rate_pct": _pct(iva_rate)},
                source_url=src,
                as_of=asof,
            ),
            _fixed(
                "registro_tax",
                G,
                S.STATUTORY,
                rates.BUILDER_REGISTRO_EUR,
                params={"basis": "fixed_builder"},
                source_url=src,
                as_of=asof,
            ),
            _fixed(
                "ipotecaria_catastale",
                G,
                S.STATUTORY,
                rates.BUILDER_IPOTECARIA_EUR + rates.BUILDER_CATASTALE_EUR,
                params={"each_eur": rates.BUILDER_IPOTECARIA_EUR},
                source_url=src,
                as_of=asof,
            ),
        ]

    rate = rates.REGISTRO_RATE_PRIMA_CASA if prima else rates.REGISTRO_RATE_OTHER
    floor = rates.REGISTRO_MIN_EUR
    ipo_cat = _fixed(
        "ipotecaria_catastale",
        G,
        S.STATUTORY,
        rates.IPOTECARIA_EUR + rates.CATASTALE_EUR,
        params={"each_eur": rates.IPOTECARIA_EUR},
        source_url=src,
        as_of=asof,
    )

    if s.rendita_eur is not None:
        multiplier = (
            rates.CADASTRAL_MULTIPLIER_PRIMA_CASA if prima else rates.CADASTRAL_MULTIPLIER_OTHER
        )
        cadastral_value = _r(s.rendita_eur * rates.CADASTRAL_REVALUATION * multiplier)
        raw = rate * cadastral_value
        registro = _fixed(
            "registro_tax",
            G,
            S.STATUTORY,
            max(raw, floor),
            params={
                "basis": "cadastral",
                "rate_pct": _pct(rate),
                "cadastral_value_eur": cadastral_value,
                "minimum_applied": raw < floor,
            },
            source_url=src,
            as_of=asof,
        )
        return [registro, ipo_cat]

    # No rendita: do not guess one. Without a prezzo-valore request the base is the declared
    # price, so rate × price is the ceiling; the per-deed minimum is the floor. The headline
    # budgets the ceiling (central = high) so a missing input never understates the cash needed.
    ceiling = max(rate * s.price_eur, floor)
    if ceiling <= floor:
        registro = _fixed(
            "registro_tax",
            G,
            S.STATUTORY,
            floor,
            params={"basis": "price", "rate_pct": _pct(rate), "minimum_applied": True},
            source_url=src,
            as_of=asof,
        )
    else:
        registro = _line(
            "registro_tax",
            G,
            S.NEEDS_INPUT,
            floor,
            ceiling,
            ceiling,
            params={
                "basis": "price_ceiling",
                "rate_pct": _pct(rate),
                "floor_eur": _r(floor),
                "ceiling_eur": _r(ceiling),
            },
            source_url=src,
            as_of=asof,
        )
        notes.append(
            CostNote(
                code="rendita_missing",
                params={"floor_eur": _r(floor), "ceiling_eur": _r(ceiling)},
            )
        )
    return [registro, ipo_cat]


def _preliminary() -> CostLine:
    amount = rates.PRELIMINARY_REGISTRO_EUR + rates.PRELIMINARY_BOLLO_EUR * (
        rates.PRELIMINARY_BOLLO_STAMPS
    )
    return _fixed(
        "preliminary_contract",
        CostGroup.CLOSING,
        CostStatus.STATUTORY,
        amount,
        params={"stamps": rates.PRELIMINARY_BOLLO_STAMPS},
        source_url=rates.AGENZIA_ENTRATE_GUIDE_URL,
        as_of=rates.STATUTORY_AS_OF,
    )


def _notary_purchase(price: float, today: date | None, notes: list[CostNote]) -> CostLine:
    band, above = _band_for(rates.NOTARY_PURCHASE_BANDS, price)
    if above:
        notes.append(
            CostNote(
                code="notary_above_published_band",
                params={"kind": "purchase", "band_max_eur": band.max_eur},
            )
        )
    return _benchmark_line(
        "notary_purchase",
        CostGroup.CLOSING,
        band.benchmark,
        today=today,
        scale=1 + rates.IVA_ORDINARY,
        params={
            "vat_pct": _pct(rates.IVA_ORDINARY),
            "band_max_eur": band.max_eur,
            "above_band": above,
        },
    )


def _agency(s: PurchaseScenario) -> list[CostLine]:
    pct = rates.AGENCY_DEFAULT_PCT if s.agency_pct is None else s.agency_pct
    if pct <= 0:
        return []
    vat = 1 + rates.IVA_ORDINARY
    fee = s.price_eur * pct / 100 * vat
    low = s.price_eur * min(pct, rates.AGENCY_LOW_PCT) / 100 * vat
    bench = rates.AGENCY_COMMISSION_PCT
    # The percentage is the buyer's own input, so only the default (what an untouched form
    # sends) can go stale; a figure the buyer chose is theirs to check, not ours.
    relies_on_default = s.agency_pct is None or s.agency_pct == rates.AGENCY_DEFAULT_PCT
    return [
        _line(
            "agency_fee",
            CostGroup.CLOSING,
            CostStatus.CONTRACTUAL,
            low,
            fee,
            fee,
            params={"pct": pct, "vat_pct": _pct(rates.IVA_ORDINARY)},
            as_of=bench.as_of,
            due=bench.due if relies_on_default else None,
            today=s.as_of,
        )
    ]


def _ltv_notes(s: PurchaseScenario, mortgage: float, prima: bool) -> list[CostNote]:
    """General LTV rules as information. Nothing here looks at the buyer — only at the share of
    the price borrowed and the tax regime already applied — and no note says the buyer can or
    cannot get a loan. The thresholds are compared in whole euros through ``mortgage_at_ltv``,
    the same rounding that turns an LTV into an amount, so exactly 80 % is never "above 80 %".
    """
    if mortgage <= 0:
        return []
    ltv = _r(mortgage / s.price_eur * 100)
    limit = rates.MUTUO_FONDIARIO_MAX_LTV_PCT
    notes: list[CostNote] = []

    if mortgage > mortgage_at_ltv(s.price_eur, limit):
        notes.append(
            CostNote(
                code="ltv_above_limit",
                params={
                    "ltv_pct": ltv,
                    "limit_pct": limit,
                    "max_pct": rates.MUTUO_FONDIARIO_MAX_LTV_WITH_GUARANTEES_PCT,
                },
                source_url=rates.BANCA_ITALIA_CIRC_229_URL,
            )
        )
        # `prima` already folds in the A/1, A/8, A/9 exclusion (Consap excludes the same three).
        consap_open = s.as_of is None or s.as_of <= rates.CONSAP_VALID_UNTIL
        if (
            prima
            and consap_open
            and s.category not in rates.CONSAP_EXCLUDED_CATEGORIES
            and mortgage <= rates.CONSAP_MAX_LOAN_EUR
        ):
            notes.append(
                CostNote(
                    code="consap_fund",
                    params={
                        "guarantee_pct": rates.CONSAP_GUARANTEE_PCT,
                        "max_loan_eur": rates.CONSAP_MAX_LOAN_EUR,
                        "enhanced_guarantee_pct": rates.CONSAP_ENHANCED_GUARANTEE_PCT,
                        "disability_from": rates.CONSAP_DISABILITY_CATEGORIES_FROM.isoformat(),
                        "disability_isee_eur": rates.CONSAP_DISABILITY_ENHANCED_ISEE_EUR,
                        "isee_eur": rates.CONSAP_ENHANCED_ISEE_EUR,
                        "min_ltv_pct": rates.CONSAP_ENHANCED_MIN_LTV_PCT,
                        "valid_until": rates.CONSAP_VALID_UNTIL.isoformat(),
                    },
                    source_url=rates.CONSAP_FUND_URL,
                )
            )

    if mortgage >= mortgage_at_ltv(s.price_eur, 100):
        notes.append(CostNote(code="ltv_full_financing", params={"ltv_pct": ltv}))

    notes.append(
        CostNote(
            code="ltv_on_price_not_appraisal",
            params={"ltv_pct": ltv},
            source_url=rates.BANCA_ITALIA_CIRC_229_URL,
        )
    )
    return notes


def _mortgage(
    s: PurchaseScenario, mortgage: float, prima: bool, notes: list[CostNote]
) -> list[CostLine]:
    if mortgage <= 0:
        return []
    G = CostGroup.CLOSING
    today = s.as_of
    sub_rate = rates.IMPOSTA_SOSTITUTIVA_PRIMA_CASA if prima else rates.IMPOSTA_SOSTITUTIVA_OTHER
    band, above = _band_for(rates.NOTARY_MORTGAGE_BANDS, mortgage)
    if above:
        notes.append(
            CostNote(
                code="notary_above_published_band",
                params={"kind": "mortgage", "band_max_eur": band.max_eur},
            )
        )
    return [
        _fixed(
            "substitute_tax_mortgage",
            G,
            CostStatus.STATUTORY,
            mortgage * sub_rate,
            params={"rate_pct": _pct(sub_rate)},
            source_url=rates.AGENZIA_ENTRATE_GUIDE_URL,
            as_of=rates.STATUTORY_AS_OF,
        ),
        _benchmark_line(
            "notary_mortgage",
            G,
            band.benchmark,
            today=today,
            scale=1 + rates.IVA_ORDINARY,
            plus=rates.TASSA_IPOTECARIA_MORTGAGE_EUR,
            params={
                "vat_pct": _pct(rates.IVA_ORDINARY),
                "tassa_ipotecaria_eur": rates.TASSA_IPOTECARIA_MORTGAGE_EUR,
                "band_max_eur": band.max_eur,
                "above_band": above,
            },
        ),
        _benchmark_line("mortgage_instruction_fee", G, rates.MORTGAGE_INSTRUCTION_FEE, today=today),
        _benchmark_line("mortgage_appraisal", G, rates.MORTGAGE_APPRAISAL, today=today),
        _benchmark_line("fire_insurance", G, rates.MORTGAGE_FIRE_INSURANCE, today=today),
    ]


def monthly_payment(principal: float, rate_pct: float, years: int) -> float:
    """The constant monthly instalment of a French-amortisation loan, unrounded.

    ``i`` is the monthly rate (the nominal annual rate / 12) and ``n`` the number of monthly
    instalments: ``P · i / (1 − (1 + i)⁻ⁿ)``. At a 0 % rate this is ``P / n`` (the formula's
    limit; ``i = 0`` would divide by zero).
    """
    n = years * rates.MONTHS_PER_YEAR
    i = rate_pct / 100 / rates.MONTHS_PER_YEAR
    if i == 0:
        return principal / n
    return principal * i / (1 - (1 + i) ** -n)


def _mortgage_payment(
    s: PurchaseScenario,
    mortgage: float,
    mortgage_lines: list[CostLine],
    notes: list[CostNote],
) -> MortgagePayment | None:
    """Rata e costo del mutuo: the payment, the interest, and the cost of the loan as a whole.

    Only with a mortgage *and* a rate: the rate is the buyer's own input and is never guessed,
    so without one a note says so (with the market reference, as a pointer and not a default).
    Totals follow from the exact instalment (rounded once, at the end), so the interest is never
    off by the cents that rounding the instalment first would add up to over the term.
    """
    if mortgage <= 0:
        return None
    if s.mortgage_rate_pct is None:
        notes.append(
            CostNote(
                code="mortgage_rate_missing",
                params={
                    "reference_pct": rates.MORTGAGE_RATE_AVERAGE.central,
                    "data_month": rates.MORTGAGE_RATE_DATA_MONTH.isoformat(),
                },
                source_url=rates.MORTGAGE_RATE_SOURCE_URL,
            )
        )
        return None

    years = s.mortgage_years or rates.MORTGAGE_DEFAULT_YEARS
    months = years * rates.MONTHS_PER_YEAR
    instalment = monthly_payment(mortgage, s.mortgage_rate_pct, years)
    repaid = _r(instalment * months)
    interest = _r(repaid - mortgage)
    costs = _sum(mortgage_lines)
    # The payment is computed at the rate given, today: a variable rate moves it. The note is
    # shown with every payment because the calculator cannot know which kind of rate it is.
    notes.append(
        CostNote(
            code="mortgage_variable_rate",
            params={"rate_pct": s.mortgage_rate_pct, "years": years},
        )
    )
    return MortgagePayment(
        years=years,
        months=months,
        rate_pct=s.mortgage_rate_pct,
        monthly_payment_eur=_r(instalment),
        total_interest_eur=interest,
        total_repaid_eur=repaid,
        mortgage_costs=costs,
        total_cost=CostRange(
            low_eur=_r(interest + costs.low_eur),
            central_eur=_r(interest + costs.central_eur),
            high_eur=_r(interest + costs.high_eur),
        ),
    )


# ---------------------------------------------------------------------------
# Renovation
# ---------------------------------------------------------------------------


def _renovation(s: PurchaseScenario) -> list[CostLine]:
    if s.renovation_tier is RenovationTier.NONE:
        return []
    G = CostGroup.RENOVATION
    tier = rates.RENOVATION_TIERS[s.renovation_tier]
    src = rates.RENOVATION_SOURCE_URL
    tiers_review = rates.RENOVATION_TIERS_REVIEW
    asof = tiers_review.as_of
    today = s.as_of
    area = s.surface_m2

    works = (area * tier.low_eur_m2, area * tier.central_eur_m2, area * tier.high_eur_m2)
    c = rates.RENOVATION_CONTINGENCY_PCT
    contingency = tuple(w * p / 100 for w, p in zip(works, (c.low, c.central, c.high), strict=True))
    iva = tuple((w + k) * rates.RENOVATION_IVA for w, k in zip(works, contingency, strict=True))
    fees = rates.RENOVATION_TECHNICAL_FEES
    ind = CostStatus.INDICATIVE

    return [
        _line(
            "renovation_works",
            G,
            ind,
            *works,
            params={
                "tier": s.renovation_tier.value,
                "surface_m2": area,
                "eur_m2_low": tier.low_eur_m2,
                "eur_m2_central": tier.central_eur_m2,
                "eur_m2_high": tier.high_eur_m2,
                "open_ended": tier.open_ended,
            },
            source_url=src,
            as_of=asof,
            due=tiers_review.due,
            today=today,
        ),
        _line(
            "renovation_contingency",
            G,
            ind,
            *contingency,
            params={"pct_low": c.low, "pct_central": c.central, "pct_high": c.high},
            source_url=src,
            as_of=c.as_of,
            due=c.due,
            today=today,
        ),
        # The IVA rate is statutory and the base is on the lines above, which carry the review
        # flag: this line has no review interval of its own.
        _line(
            "renovation_vat",
            G,
            ind,
            *iva,
            params={"rate_pct": _pct(rates.RENOVATION_IVA)},
            # The rule, not the price page: the 10 % is statutory.
            source_url=rates.AGENZIA_ENTRATE_RENOVATION_GUIDE_URL,
            as_of=asof,
        ),
        *(
            [_benchmark_line("renovation_fees", G, fees, today=today)]
            if s.renovation_tier in rates.RENOVATION_TIERS_WITH_TECHNICAL_FEES
            else []
        ),
    ]


def _renovation_vat_note() -> CostNote:
    """The renovation IVA is a flat reduced rate here; part of a renovation can be at the ordinary
    one (beni significativi beyond the limit, materials bought directly). Disclosed, not modelled:
    see ``rates.RENOVATION_IVA``."""
    return CostNote(
        code="renovation_vat_partial",
        params={
            "rate_pct": _pct(rates.RENOVATION_IVA),
            "ordinary_rate_pct": _pct(rates.IVA_ORDINARY),
        },
        source_url=rates.AGENZIA_ENTRATE_RENOVATION_GUIDE_URL,
    )


def _bonus(s: PurchaseScenario, prima: bool, renovation: list[CostLine]) -> list[CostLine]:
    """Bonus ristrutturazione per payment year — an offset, reported apart from the totals."""
    if not renovation or s.renovation_tier not in rates.BONUS_ELIGIBLE_TIERS:
        return []
    spend = (
        sum(ln.low_eur for ln in renovation),
        sum(ln.central_eur for ln in renovation),
        sum(ln.high_eur for ln in renovation),
    )
    out: list[CostLine] = []
    for year, rule in sorted(rates.BONUS_RISTRUTTURAZIONE.items()):
        if s.as_of is not None and year < s.as_of.year:
            continue
        pct = rule.main_residence_pct if prima else rule.other_pct
        low, central, high = (pct / 100 * min(x, rates.BONUS_SPEND_CAP_EUR) for x in spend)
        out.append(
            _line(
                "renovation_bonus",
                CostGroup.OFFSET,
                CostStatus.INDICATIVE if rule.provisional else CostStatus.STATUTORY,
                low,
                central,
                high,
                params={
                    "year": year,
                    "rate_pct": pct,
                    "years": rates.BONUS_INSTALMENT_YEARS,
                    "cap_eur": rates.BONUS_SPEND_CAP_EUR,
                    "main_residence": prima,
                    "provisional": rule.provisional,
                },
                source_url=rule.source_url,
                as_of=rule.as_of,
            )
        )
    return out


def _overdue_notes(lines: Iterable[CostLine]) -> list[CostNote]:
    """One note when ranges are past their review date: how many, and when the oldest of them
    was last reviewed. Which lines they are is on the lines themselves (``CostLine.stale``)."""
    overdue = [ln for ln in lines if ln.stale]
    if not overdue:
        return []
    oldest = min(ln.as_of for ln in overdue if ln.as_of is not None)
    return [
        CostNote(
            code="ranges_overdue",
            params={"count": len(overdue), "last_reviewed": oldest.isoformat()},
        )
    ]


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def _sum(lines: Iterable[CostLine]) -> CostRange:
    low = central = high = 0.0
    for ln in lines:
        low += ln.low_eur
        central += ln.central_eur
        high += ln.high_eur
    return CostRange(low_eur=_r(low), central_eur=_r(central), high_eur=_r(high))


def compute(s: PurchaseScenario) -> CostBreakdown:
    notes: list[CostNote] = []

    # A/1, A/8, A/9 cannot use the prima-casa regime even when the buyer would otherwise
    # qualify; the ordinary rates apply to registro, IVA and the mortgage's imposta sostitutiva.
    prima = s.prima_casa
    if prima and s.category in rates.PRIMA_CASA_EXCLUDED_CATEGORIES:
        prima = False
        notes.append(
            CostNote(code="prima_casa_excluded_category", params={"category": s.category.value})
        )

    # The one normalised mortgage amount: everything below reads this, not the raw inputs.
    mortgage = s.mortgage_amount_eur

    mortgage_lines = _mortgage(s, mortgage, prima, notes)
    closing = [
        *_purchase_taxes(s, prima, notes),
        _preliminary(),
        _notary_purchase(s.price_eur, s.as_of, notes),
        *_agency(s),
        *mortgage_lines,
        _benchmark_line(
            "conformity_check", CostGroup.CLOSING, rates.CONFORMITY_CHECK, today=s.as_of
        ),
        _benchmark_line(
            "moving_utilities", CostGroup.CLOSING, rates.MOVING_AND_UTILITIES, today=s.as_of
        ),
    ]
    renovation = _renovation(s)
    offsets = _bonus(s, prima, renovation)

    closing_total = _sum(closing)
    renovation_total = _sum(renovation)
    total = _sum([*closing, *renovation])
    notes.extend(_ltv_notes(s, mortgage, prima))
    payment = _mortgage_payment(s, mortgage, mortgage_lines, notes)
    if renovation:
        notes.append(_renovation_vat_note())
    notes.extend(_overdue_notes([*closing, *renovation]))
    down_payment = _r(s.price_eur - mortgage)
    return CostBreakdown(
        price_eur=s.price_eur,
        mortgage_eur=mortgage,
        mortgage_ltv_pct=_r(mortgage / s.price_eur * 100),
        down_payment_eur=down_payment,
        lines=[*closing, *renovation, *offsets],
        notes=notes,
        closing=closing_total,
        renovation=renovation_total,
        total_on_top=total,
        cash_needed=CostRange(
            low_eur=_r(down_payment + total.low_eur),
            central_eur=_r(down_payment + total.central_eur),
            high_eur=_r(down_payment + total.high_eur),
        ),
        mortgage_payment=payment,
        rates_as_of=rates.RATES_AS_OF,
    )
