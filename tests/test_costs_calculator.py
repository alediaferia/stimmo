"""Purchase-cost calculator: statutory worked examples, edge cases and the rates contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from stimmo.costs import rates
from stimmo.costs.calculator import compute
from stimmo.models import (
    CadastralCategory,
    CostBreakdown,
    CostGroup,
    CostLine,
    CostStatus,
    FineCondition,
    PurchaseScenario,
    RenovationTier,
    SellerType,
    mortgage_at_ltv,
)


def _scenario(**kw) -> PurchaseScenario:
    base = dict(price_eur=450_000, prima_casa=True, rendita_eur=1_100)
    base.update(kw)
    return PurchaseScenario(**base)


def _line(b: CostBreakdown, code: str, **params) -> CostLine:
    hits = [
        ln
        for ln in b.lines
        if ln.code == code and all(ln.params.get(k) == v for k, v in params.items())
    ]
    assert len(hits) == 1, (code, [ln.code for ln in b.lines])
    return hits[0]


def _has(b: CostBreakdown, code: str) -> bool:
    return any(ln.code == code for ln in b.lines)


# ---------------------------------------------------------------------------
# Worked example: EUR 450,000 resale, private seller, rendita EUR 1,100
# ---------------------------------------------------------------------------


class TestWorkedExample:
    def test_prima_casa(self):
        b = compute(_scenario(mortgage_eur=300_000))

        registro = _line(b, "registro_tax")
        assert registro.params["cadastral_value_eur"] == 127_050
        assert registro.central_eur == 2_541
        assert registro.status is CostStatus.STATUTORY
        assert registro.low_eur == registro.high_eur == 2_541

        assert _line(b, "ipotecaria_catastale").central_eur == 100
        # 3 % of 450,000 = 13,500, plus 22 % IVA.
        assert _line(b, "agency_fee").central_eur == 16_470
        # Imposta sostitutiva 0.25 % of the 300,000 loan.
        assert _line(b, "substitute_tax_mortgage").central_eur == 750
        # A mortgage at 66.7 % of the price: only the "computed on the price" LTV note applies
        # (and, with no rate given, the note that the payment needs one).
        assert [n.code for n in b.notes if n.code.startswith("ltv_")] == [
            "ltv_on_price_not_appraisal"
        ]
        assert [n.code for n in b.notes if n.code.startswith("mortgage_")] == [
            "mortgage_rate_missing"
        ]

    def test_seconda_casa(self):
        b = compute(_scenario(prima_casa=False, mortgage_eur=300_000))

        registro = _line(b, "registro_tax")
        assert registro.params["cadastral_value_eur"] == 138_600  # 1,100 x 1.05 x 120
        assert registro.central_eur == 12_474  # 9 %
        assert _line(b, "substitute_tax_mortgage").central_eur == 6_000  # 2 %

    def test_closing_subtotal_is_the_sum_of_its_lines(self):
        b = compute(_scenario(mortgage_eur=300_000))
        closing = b.lines_in(CostGroup.CLOSING)
        assert b.closing.central_eur == pytest.approx(sum(ln.central_eur for ln in closing))
        assert b.closing.low_eur == pytest.approx(sum(ln.low_eur for ln in closing))
        assert b.closing.high_eur == pytest.approx(sum(ln.high_eur for ln in closing))
        # Closing costs for this buyer are in the low-30s of thousands at most.
        assert 28_000 < b.closing.central_eur < 29_500

    def test_every_line_is_ordered_and_non_negative(self):
        b = compute(
            _scenario(mortgage_eur=300_000, surface_m2=70, renovation_tier=RenovationTier.FULL)
        )
        for ln in b.lines:
            assert 0 <= ln.low_eur <= ln.central_eur <= ln.high_eur, ln.code


# ---------------------------------------------------------------------------
# Registro: minimum, missing rendita, excluded categories
# ---------------------------------------------------------------------------


class TestRegistro:
    def test_minimum_per_deed(self):
        # 200 x 1.05 x 110 = 23,100; 2 % = 462 < 1,000.
        b = compute(_scenario(rendita_eur=200))
        registro = _line(b, "registro_tax")
        assert registro.central_eur == rates.REGISTRO_MIN_EUR == 1_000
        assert registro.params["minimum_applied"] is True

    def test_missing_rendita_is_bounded_not_estimated(self):
        b = compute(_scenario(rendita_eur=None))
        registro = _line(b, "registro_tax")
        assert registro.status is CostStatus.NEEDS_INPUT
        assert registro.low_eur == 1_000  # per-deed minimum
        assert registro.high_eur == 9_000  # 2 % of the price: the ceiling
        assert registro.central_eur == registro.high_eur  # the totals budget the ceiling
        assert registro.params["basis"] == "price_ceiling"
        assert [n.code for n in b.notes] == ["rendita_missing"]
        assert b.notes[0].params == {"floor_eur": 1_000, "ceiling_eur": 9_000}

    def test_missing_rendita_does_not_invent_a_cadastral_value(self):
        registro = _line(compute(_scenario(rendita_eur=None)), "registro_tax")
        assert "cadastral_value_eur" not in registro.params

    def test_missing_rendita_seconda_casa_ceiling_is_nine_percent(self):
        registro = _line(compute(_scenario(rendita_eur=None, prima_casa=False)), "registro_tax")
        assert registro.high_eur == 40_500

    def test_a1_cannot_use_prima_casa(self):
        b = compute(_scenario(category=CadastralCategory.A1, mortgage_eur=300_000, prima_casa=True))
        # Ordinary rates: 9 % on 1,100 x 1.05 x 120, and 2 % imposta sostitutiva.
        assert _line(b, "registro_tax").central_eur == 12_474
        assert _line(b, "substitute_tax_mortgage").central_eur == 6_000
        assert [n.code for n in b.notes if n.code != "mortgage_rate_missing"] == [
            "prima_casa_excluded_category",
            "ltv_on_price_not_appraisal",
        ]
        assert b.notes[0].params == {"category": "A/1"}

    @pytest.mark.parametrize("category", [CadastralCategory.A8, CadastralCategory.A9])
    def test_a8_a9_are_excluded_too(self, category):
        b = compute(_scenario(category=category))
        assert _line(b, "registro_tax").central_eur == 12_474

    def test_ordinary_category_keeps_prima_casa(self):
        b = compute(_scenario(category=CadastralCategory.A3))
        assert _line(b, "registro_tax").central_eur == 2_541
        assert b.notes == []


# ---------------------------------------------------------------------------
# Builder selling under IVA
# ---------------------------------------------------------------------------


class TestBuilderVat:
    def test_prima_casa_pays_four_percent_iva_and_fixed_taxes(self):
        b = compute(_scenario(seller=SellerType.BUILDER_VAT, rendita_eur=None))
        assert _line(b, "vat_builder").central_eur == 18_000  # 4 % of 450,000
        assert _line(b, "registro_tax").central_eur == 200
        assert _line(b, "ipotecaria_catastale").central_eur == 400  # 200 + 200
        # No prezzo-valore here, so a missing rendita is irrelevant.
        assert b.notes == []
        assert _line(b, "registro_tax").status is CostStatus.STATUTORY

    def test_other_homes_pay_ten_percent(self):
        b = compute(_scenario(seller=SellerType.BUILDER_VAT, prima_casa=False))
        assert _line(b, "vat_builder").central_eur == 45_000

    def test_luxury_categories_pay_twenty_two_percent(self):
        b = compute(_scenario(seller=SellerType.BUILDER_VAT, category=CadastralCategory.A1))
        vat = _line(b, "vat_builder")
        assert vat.central_eur == 99_000
        assert vat.params["rate_pct"] == 22

    def test_the_three_fixed_taxes_total_six_hundred(self):
        b = compute(_scenario(seller=SellerType.BUILDER_VAT))
        fixed = _line(b, "registro_tax").central_eur + _line(b, "ipotecaria_catastale").central_eur
        assert fixed == 600


# ---------------------------------------------------------------------------
# Notary bands
# ---------------------------------------------------------------------------


class TestNotary:
    def test_purchase_band_up_to_465k(self):
        line = _line(compute(_scenario()), "notary_purchase")
        assert line.status is CostStatus.INDICATIVE
        assert line.params["above_band"] is False
        assert line.high_eur == pytest.approx(3_730 * 1.22)

    def test_lower_band_up_to_370k(self):
        line = _line(compute(_scenario(price_eur=300_000)), "notary_purchase")
        assert line.low_eur == pytest.approx(2_250 * 1.22)
        assert line.high_eur == pytest.approx(3_360 * 1.22)

    def test_price_above_the_published_bands_is_flagged_not_extrapolated(self):
        b = compute(_scenario(price_eur=800_000))
        line = _line(b, "notary_purchase")
        assert line.params["above_band"] is True
        assert line.high_eur == pytest.approx(3_730 * 1.22)  # top band, unchanged
        assert [n.code for n in b.notes] == ["notary_above_published_band"]
        assert b.notes[0].params == {"kind": "purchase", "band_max_eur": 465_000}

    def test_mortgage_notary_adds_iva_and_tassa_ipotecaria(self):
        line = _line(compute(_scenario(mortgage_eur=300_000)), "notary_mortgage")
        assert line.low_eur == pytest.approx(1_750 * 1.22 + 35)
        assert line.central_eur == pytest.approx(2_000 * 1.22 + 35)
        assert line.high_eur == pytest.approx(2_300 * 1.22 + 35)

    def test_mortgage_above_the_published_band_is_flagged(self):
        b = compute(_scenario(price_eur=900_000, mortgage_eur=500_000))
        kinds = [n.params["kind"] for n in b.notes if n.code == "notary_above_published_band"]
        assert sorted(kinds) == ["mortgage", "purchase"]


# ---------------------------------------------------------------------------
# Agency, mortgage, optional lines
# ---------------------------------------------------------------------------


class TestAgencyAndMortgage:
    def test_agency_default_is_three_percent_plus_iva(self):
        line = _line(compute(_scenario()), "agency_fee")
        assert line.central_eur == 16_470
        assert line.low_eur == pytest.approx(450_000 * 0.02 * 1.22)  # bottom of the 2-3 % range
        assert line.high_eur == line.central_eur
        assert line.status is CostStatus.CONTRACTUAL

    def test_agency_percentage_is_user_editable(self):
        line = _line(compute(_scenario(agency_pct=2.0)), "agency_fee")
        assert line.central_eur == pytest.approx(10_980)

    def test_no_agency_no_line(self):
        assert not _has(compute(_scenario(agency_pct=0)), "agency_fee")

    def test_no_mortgage_no_mortgage_lines(self):
        b = compute(_scenario())
        for code in (
            "substitute_tax_mortgage",
            "notary_mortgage",
            "mortgage_instruction_fee",
            "mortgage_appraisal",
            "fire_insurance",
        ):
            assert not _has(b, code)

    def test_mortgage_cannot_exceed_price(self):
        with pytest.raises(ValidationError):
            _scenario(mortgage_eur=500_000)

    def test_cash_needed_is_price_minus_loan_plus_costs_without_the_bonus(self):
        s = _scenario(mortgage_eur=300_000, surface_m2=70, renovation_tier=RenovationTier.MEDIUM)
        b = compute(s)
        assert b.cash_needed.central_eur == pytest.approx(150_000 + b.total_on_top.central_eur)
        assert b.cash_needed.low_eur == pytest.approx(150_000 + b.total_on_top.low_eur)


# ---------------------------------------------------------------------------
# Renovation and bonus
# ---------------------------------------------------------------------------


class TestRenovation:
    def test_medium_70m2_at_the_central_800_per_m2(self):
        b = compute(_scenario(surface_m2=70, renovation_tier=RenovationTier.MEDIUM))
        works = _line(b, "renovation_works")
        assert works.central_eur == 56_000  # 70 x 800
        assert (works.low_eur, works.high_eur) == (42_000, 70_000)  # 600 / 1,000 per m2
        assert _line(b, "renovation_vat").central_eur == 6_160  # 10 % of works + contingency
        assert _line(b, "renovation_fees").central_eur == 4_000
        contingency = _line(b, "renovation_contingency")
        assert contingency.central_eur == 5_600  # 10 % of the works
        assert (contingency.low_eur, contingency.high_eur) == (3_360, 8_400)  # 8 % / 12 %
        assert b.renovation.central_eur == 71_760
        # Works + IVA on the works + fees is the plain 65,600; the contingency (with its IVA)
        # is the only thing on top of that.
        plain = 56_000 + 56_000 * 0.10 + 4_000
        assert plain == 65_600
        assert b.renovation.central_eur == pytest.approx(plain + 5_600 * 1.10)

    def test_renovation_lines_are_all_indicative(self):
        b = compute(_scenario(surface_m2=70, renovation_tier=RenovationTier.FULL))
        for ln in b.lines_in(CostGroup.RENOVATION):
            assert ln.status is CostStatus.INDICATIVE

    @pytest.mark.parametrize(
        ("tier", "low", "high"),
        [
            (RenovationTier.LIGHT, 250, 500),
            (RenovationTier.MEDIUM, 600, 1_000),
            (RenovationTier.FULL, 1_000, 1_500),
            (RenovationTier.PREMIUM, 1_600, 2_500),
        ],
    )
    def test_tier_per_m2_ranges(self, tier, low, high):
        works = _line(compute(_scenario(surface_m2=100, renovation_tier=tier)), "renovation_works")
        assert works.low_eur == low * 100
        assert works.high_eur == high * 100
        assert works.central_eur == (low + high) / 2 * 100

    def test_the_tier_bounds_are_the_ones_in_the_cited_table(self):
        """Light 250-500, partial 600-1.000, complete 1.000-1.500, complex/prestige 1.600-2.500+
        (ghle.it, "Fascia orientativa"). The full tier once said 1,600: the premium tier's
        lower bound, not the source's. The gap between 1,500 and 1,600 is the source's own."""
        bounds = {t: (r.low_eur_m2, r.high_eur_m2) for t, r in rates.RENOVATION_TIERS.items()}
        assert bounds == {
            RenovationTier.LIGHT: (250, 500),
            RenovationTier.MEDIUM: (600, 1_000),
            RenovationTier.FULL: (1_000, 1_500),
            RenovationTier.PREMIUM: (1_600, 2_500),
        }
        assert rates.RENOVATION_TIERS[RenovationTier.PREMIUM].open_ended
        assert not any(
            rates.RENOVATION_TIERS[t].open_ended
            for t in rates.RENOVATION_TIERS
            if t.value != "premium"
        )
        assert rates.RENOVATION_TIERS[RenovationTier.FULL].central_eur_m2 == 1_250
        assert (
            rates.RENOVATION_SOURCE_URL
            == "https://www.ghle.it/post/quanto-costa-ristrutturare-casa-milano-2026"
        )

    def test_full_tier_70m2_worked_numbers(self):
        b = compute(_scenario(surface_m2=70, renovation_tier=RenovationTier.FULL))
        works = _line(b, "renovation_works")
        assert (works.low_eur, works.central_eur, works.high_eur) == (70_000, 87_500, 105_000)
        assert works.params["eur_m2_high"] == 1_500

    def test_light_tier_has_no_technical_fee(self):
        # Light works are edilizia libera (DPR 380/2001 art. 6): no CILA, no technician.
        b = compute(_scenario(surface_m2=70, renovation_tier=RenovationTier.LIGHT))
        assert not _has(b, "renovation_fees")
        works = _line(b, "renovation_works").central_eur  # 70 x 375
        contingency = _line(b, "renovation_contingency").central_eur
        vat = _line(b, "renovation_vat").central_eur
        assert works == 26_250
        assert b.renovation.central_eur == pytest.approx(works + contingency + vat)
        assert b.renovation.low_eur == pytest.approx(
            sum(ln.low_eur for ln in b.lines_in(CostGroup.RENOVATION))
        )

    @pytest.mark.parametrize(
        "tier", [RenovationTier.MEDIUM, RenovationTier.FULL, RenovationTier.PREMIUM]
    )
    def test_medium_and_above_keep_the_technical_fee(self, tier):
        b = compute(_scenario(surface_m2=70, renovation_tier=tier))
        fees = _line(b, "renovation_fees")
        assert (fees.low_eur, fees.central_eur, fees.high_eur) == (3_000, 4_000, 6_000)

    def test_technical_fee_tiers_exclude_exactly_the_light_tier(self):
        assert rates.RENOVATION_TIERS_WITH_TECHNICAL_FEES == (
            set(rates.RENOVATION_TIERS) - {RenovationTier.LIGHT}
        )

    def test_premium_is_flagged_open_ended(self):
        works = _line(
            compute(_scenario(surface_m2=50, renovation_tier=RenovationTier.PREMIUM)),
            "renovation_works",
        )
        assert works.params["open_ended"] is True

    def test_no_renovation_no_lines_no_bonus(self):
        b = compute(_scenario())
        assert b.lines_in(CostGroup.RENOVATION) == []
        assert b.lines_in(CostGroup.OFFSET) == []
        assert b.renovation.central_eur == 0
        assert b.total_on_top.central_eur == b.closing.central_eur

    def test_renovation_needs_a_surface(self):
        with pytest.raises(ValidationError):
            _scenario(renovation_tier=RenovationTier.LIGHT, surface_m2=0)

    def test_total_is_closing_plus_renovation(self):
        b = compute(_scenario(surface_m2=70, renovation_tier=RenovationTier.MEDIUM))
        assert b.total_on_top.central_eur == pytest.approx(
            b.closing.central_eur + b.renovation.central_eur
        )


class TestBonus:
    def _bonus(self, **kw) -> dict[int, CostLine]:
        b = compute(_scenario(surface_m2=70, renovation_tier=RenovationTier.MEDIUM, **kw))
        return {ln.params["year"]: ln for ln in b.lines_in(CostGroup.OFFSET)}

    def test_rates_by_payment_year_main_residence(self):
        bonus = self._bonus()
        assert bonus[2026].params["rate_pct"] == 50
        assert bonus[2027].params["rate_pct"] == 36
        # 50 % and 36 % of the renovation subtotal (71,760).
        assert bonus[2026].central_eur == pytest.approx(35_880)
        assert bonus[2027].central_eur == pytest.approx(25_833.60)

    def test_other_homes_get_the_lower_rates(self):
        bonus = self._bonus(prima_casa=False)
        assert bonus[2026].params["rate_pct"] == 36
        assert bonus[2027].params["rate_pct"] == 30

    def test_2027_is_provisional_and_labelled_so(self):
        bonus = self._bonus()
        assert bonus[2027].params["provisional"] is True
        assert bonus[2027].status is CostStatus.INDICATIVE
        assert bonus[2026].params["provisional"] is False
        assert bonus[2026].status is CostStatus.STATUTORY

    def test_spend_cap_and_ten_years(self):
        b = compute(
            _scenario(price_eur=900_000, surface_m2=200, renovation_tier=RenovationTier.PREMIUM)
        )
        bonus = {ln.params["year"]: ln for ln in b.lines_in(CostGroup.OFFSET)}
        assert bonus[2026].central_eur == 0.5 * 96_000  # capped
        assert bonus[2026].params["years"] == 10
        assert bonus[2026].params["cap_eur"] == 96_000

    def test_the_bonus_is_never_netted_from_the_totals(self):
        s = dict(surface_m2=70, renovation_tier=RenovationTier.MEDIUM)
        with_bonus = compute(_scenario(**s))
        assert with_bonus.lines_in(CostGroup.OFFSET)
        lines_total = sum(
            ln.central_eur
            for ln in with_bonus.lines
            if ln.group in (CostGroup.CLOSING, CostGroup.RENOVATION)
        )
        assert with_bonus.total_on_top.central_eur == pytest.approx(lines_total)

    def test_light_works_carry_no_bonus_on_their_own(self):
        # Ordinary maintenance of a single unit: art. 16-bis c.1 lett. a TUIR covers it only on
        # common parts, so the light tier has renovation lines but no offset.
        b = compute(_scenario(surface_m2=70, renovation_tier=RenovationTier.LIGHT))
        assert b.lines_in(CostGroup.RENOVATION)
        assert b.lines_in(CostGroup.OFFSET) == []

    @pytest.mark.parametrize(
        "tier", [RenovationTier.MEDIUM, RenovationTier.FULL, RenovationTier.PREMIUM]
    )
    def test_medium_full_and_premium_keep_the_bonus(self, tier):
        b = compute(_scenario(surface_m2=70, renovation_tier=tier))
        assert [ln.params["year"] for ln in b.lines_in(CostGroup.OFFSET)] == [2026, 2027]

    def test_bonus_tiers_are_exactly_the_non_light_tiers(self):
        assert rates.BONUS_ELIGIBLE_TIERS == set(rates.RENOVATION_TIERS) - {RenovationTier.LIGHT}

    def test_years_already_past_are_not_shown(self):
        b = compute(
            _scenario(surface_m2=70, renovation_tier=RenovationTier.MEDIUM, as_of=date(2027, 3, 1))
        )
        assert [ln.params["year"] for ln in b.lines_in(CostGroup.OFFSET)] == [2027]


# ---------------------------------------------------------------------------
# The rates module is the tuning surface
# ---------------------------------------------------------------------------


class TestRatesContract:
    def test_default_tier_covers_every_condition_deterministically(self):
        assert set(rates.FINE_CONDITION_DEFAULT_TIER) == set(FineCondition)
        assert rates.default_renovation_tier(FineCondition.DA_RISTRUTTURARE) is RenovationTier.FULL
        for condition in (
            FineCondition.NUOVO,
            FineCondition.RISTRUTTURATO,
            FineCondition.ABITABILE,
        ):
            assert rates.default_renovation_tier(condition) is RenovationTier.NONE

    def _benchmarks(self) -> list[rates.Benchmark]:
        out = [
            rates.CONFORMITY_CHECK,
            rates.MOVING_AND_UTILITIES,
            rates.MORTGAGE_INSTRUCTION_FEE,
            rates.MORTGAGE_APPRAISAL,
            rates.MORTGAGE_FIRE_INSURANCE,
            rates.RENOVATION_TECHNICAL_FEES,
            rates.RENOVATION_CONTINGENCY_PCT,
            rates.AGENCY_COMMISSION_PCT,
        ]
        out += [band.benchmark for band in rates.NOTARY_PURCHASE_BANDS]
        out += [band.benchmark for band in rates.NOTARY_MORTGAGE_BANDS]
        return out

    def test_every_benchmark_has_an_as_of_date_and_is_ordered(self):
        for b in self._benchmarks():
            assert isinstance(b.as_of, date)
            assert b.review_months > 0
            assert b.low <= b.central <= b.high

    def test_a_benchmark_without_a_source_url_must_be_indicative(self):
        for b in self._benchmarks():
            assert b.source_url is not None or b.indicative
        with pytest.raises(ValueError, match="indicative"):
            rates.Benchmark(1, 2, 3, None, as_of=date(2026, 10, 6), review_months=6)

    def test_warning_flagged_figures_are_indicative(self):
        # Notary bands, renovation fees/contingency, conformity and moving/utilities.
        for b in [
            *(band.benchmark for band in rates.NOTARY_PURCHASE_BANDS),
            *(band.benchmark for band in rates.NOTARY_MORTGAGE_BANDS),
            rates.RENOVATION_TECHNICAL_FEES,
            rates.RENOVATION_CONTINGENCY_PCT,
            rates.CONFORMITY_CHECK,
            rates.MOVING_AND_UTILITIES,
        ]:
            assert b.indicative

    def test_the_technical_fees_cite_no_source_because_none_states_them(self):
        """The €3-6k range is not on the page it used to cite (ghle.it has only the CILA's
        €75). No source, so no source_url, and it is indicative by construction."""
        fees = rates.RENOVATION_TECHNICAL_FEES
        assert fees.source_url is None and fees.indicative
        item = next(i for i in rates.review_schedule() if i.name == "RENOVATION_TECHNICAL_FEES")
        assert item.source_url is None and item.indicative

    @pytest.mark.parametrize("tier", [RenovationTier.MEDIUM, RenovationTier.FULL])
    def test_the_technical_fee_line_is_indicative_and_has_no_source_link(self, tier):
        b = compute(_scenario(surface_m2=70, renovation_tier=tier))
        fees = _line(b, "renovation_fees")
        assert fees.status is CostStatus.INDICATIVE
        assert fees.source_url is None

    def test_bands_are_ascending(self):
        for bands in (rates.NOTARY_PURCHASE_BANDS, rates.NOTARY_MORTGAGE_BANDS):
            maxima = [band.max_eur for band in bands]
            assert maxima == sorted(maxima)

    def test_statutory_lines_cite_the_source(self):
        b = compute(_scenario(mortgage_eur=300_000))
        for code in ("registro_tax", "ipotecaria_catastale", "substitute_tax_mortgage"):
            line = _line(b, code)
            assert line.source_url == rates.AGENZIA_ENTRATE_GUIDE_URL
            assert line.as_of == rates.STATUTORY_AS_OF

    def test_compute_is_deterministic(self):
        s = _scenario(mortgage_eur=300_000, surface_m2=70, renovation_tier=RenovationTier.MEDIUM)
        assert compute(s) == compute(s)


# ---------------------------------------------------------------------------
# Where the sources point
# ---------------------------------------------------------------------------

# Property portals are not a source for a statutory or published figure.
_PORTAL_DOMAINS = (
    "idealista.it",
    "immobiliare.it",
    "casa.it",
    "borsinoimmobiliare.it",
    "wikicasa.it",
)
# The only hosts a statutory line may cite.
_PRIMARY_DOMAINS = ("normattiva.it", "agenziaentrate.gov.it", "bancaditalia.it")


def _on(url: str, domains: tuple[str, ...]) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in domains)


def _rates_source_urls() -> dict[str, str]:
    """Every source URL declared in rates.py: URL constants, benchmarks, price bands, bonus rules
    and review items."""
    found: dict[str, str] = {}
    for name, value in vars(rates).items():
        if isinstance(value, str) and value.startswith("http"):
            found[name] = value
        elif isinstance(value, rates.Benchmark | rates.BonusRule) and value.source_url:
            found[name] = value.source_url
        elif isinstance(value, dict):
            for key, rule in value.items():
                if isinstance(rule, rates.BonusRule):
                    found[f"{name}[{key}]"] = rule.source_url
        elif (
            isinstance(value, tuple)
            and value
            and all(isinstance(v, rates.PriceBand) for v in value)
        ):
            for band in value:
                if band.benchmark.source_url:
                    found[f"{name}[<= {band.max_eur:,.0f}]"] = band.benchmark.source_url
    for item in rates.review_schedule():
        if item.source_url:
            found[item.name] = item.source_url
    return found


class TestSourceHygiene:
    def test_the_guard_recognises_a_portal_host(self):
        assert _on("https://www.idealista.it/news/x", _PORTAL_DOMAINS)
        assert _on("https://immobiliare.it/", _PORTAL_DOMAINS)
        assert not _on("https://www.normattiva.it/uri-res/N2Ls", _PORTAL_DOMAINS)
        assert not _on("https://notidealista.it.example/", _PORTAL_DOMAINS[:1])

    def test_the_helper_finds_the_urls_the_guard_is_about(self):
        found = _rates_source_urls()
        assert len(found) >= 15
        assert found["BONUS_RISTRUTTURAZIONE[2027]"] == rates.BONUS_SOURCE_URL
        assert found["MORTGAGE_RATE_AVERAGE"] == rates.MORTGAGE_RATE_SOURCE_URL

    def test_no_source_in_rates_py_is_a_property_portal(self):
        offenders = {n: u for n, u in _rates_source_urls().items() if _on(u, _PORTAL_DOMAINS)}
        assert offenders == {}

    def test_the_bonus_cites_the_statute_pinned_to_its_last_day_in_force(self):
        assert rates.BONUS_RISTRUTTURAZIONE.keys() == {2026, 2027}
        for rule in rates.BONUS_RISTRUTTURAZIONE.values():
            assert rule.source_url == rates.BONUS_SOURCE_URL
        url = rates.BONUS_SOURCE_URL
        assert _on(url, ("normattiva.it",))
        assert "decreto.legge:2013-06-04;63~art16" in url
        assert url.endswith("!vig=2026-12-31")  # keeps showing the 2025-2027 text after repeal

    def test_statutory_and_renovation_tax_lines_cite_a_primary_source(self):
        b = compute(
            _scenario(
                mortgage_eur=300_000,
                surface_m2=70,
                renovation_tier=RenovationTier.FULL,
                as_of=date(2026, 10, 8),
            )
        )
        checked = [
            ln
            for ln in b.lines
            if ln.status is CostStatus.STATUTORY
            or ln.code in ("renovation_vat", "renovation_bonus")
        ]
        assert {ln.code for ln in checked} >= {"registro_tax", "renovation_vat", "renovation_bonus"}
        for ln in checked:
            assert ln.source_url and _on(ln.source_url, _PRIMARY_DOMAINS), ln.code
        # The 2026 and 2027 bonus rows both cite the statute.
        assert {ln.params["year"] for ln in b.lines if ln.code == "renovation_bonus"} == {
            2026,
            2027,
        }

    def test_no_line_or_note_of_a_full_scenario_links_to_a_portal(self):
        b = compute(
            _scenario(mortgage_eur=400_000, surface_m2=70, renovation_tier=RenovationTier.MEDIUM)
        )
        urls = [ln.source_url for ln in b.lines if ln.source_url]
        urls += [n.source_url for n in b.notes if n.source_url]
        assert urls
        assert not [u for u in urls if _on(u, _PORTAL_DOMAINS)]


# ---------------------------------------------------------------------------
# Mortgage as a percentage of the price (LTV) and the general LTV rules
# ---------------------------------------------------------------------------


def _codes(b: CostBreakdown) -> list[str]:
    return [n.code for n in b.notes]


def _note(b: CostBreakdown, code: str):
    hits = [n for n in b.notes if n.code == code]
    assert len(hits) == 1, (code, _codes(b))
    return hits[0]


class TestMortgageInput:
    def test_amount_and_ltv_give_the_same_breakdown(self):
        # 80 % of the price and its amount in euros are the same scenario, byte for byte.
        assert compute(_scenario(mortgage_ltv_pct=80)) == compute(_scenario(mortgage_eur=360_000))

    @pytest.mark.parametrize("ltv", [0.5, 33.3, 66.67, 90, 100])
    def test_any_ltv_matches_its_normalised_amount(self, ltv: float):
        amount = mortgage_at_ltv(450_000, ltv)
        assert compute(_scenario(mortgage_ltv_pct=ltv)) == compute(_scenario(mortgage_eur=amount))

    def test_80_percent_of_450k_is_360k_with_900_euro_sostitutiva(self):
        b = compute(_scenario(mortgage_ltv_pct=80))
        assert b.mortgage_eur == 360_000
        assert b.mortgage_ltv_pct == 80
        # Imposta sostitutiva 0.25 % (prima casa) on the normalised amount.
        assert _line(b, "substitute_tax_mortgage").central_eur == 900

    def test_sostitutiva_for_a_second_home_follows_the_normalised_amount(self):
        b = compute(_scenario(prima_casa=False, mortgage_ltv_pct=80))
        assert _line(b, "substitute_tax_mortgage").central_eur == 7_200  # 2 % of 360,000

    def test_notary_band_follows_the_normalised_amount(self):
        def kinds(**kw):
            b = compute(_scenario(**kw))
            return [n.params["kind"] for n in b.notes if n.code == "notary_above_published_band"]

        assert kinds(mortgage_ltv_pct=80) == []  # 360,000 is inside the 370,000 band
        assert kinds(mortgage_ltv_pct=90) == ["mortgage"]  # 405,000 is above it

    def test_ltv_is_rounded_to_whole_euros_half_up(self):
        assert mortgage_at_ltv(100_001, 50) == 50_001  # 50,000.5: half-up, not banker's
        assert mortgage_at_ltv(100_001, 33.3) == 33_300
        assert compute(_scenario(price_eur=100_001, mortgage_ltv_pct=50)).mortgage_eur == 50_001

    def test_ltv_never_exceeds_the_price(self):
        assert mortgage_at_ltv(100_000.6, 100) == 100_000
        b = compute(_scenario(price_eur=100_000.6, mortgage_ltv_pct=100))
        assert b.down_payment_eur >= 0

    def test_no_mortgage_by_default_and_with_zero_ltv(self):
        for b in (compute(_scenario()), compute(_scenario(mortgage_ltv_pct=0))):
            assert b.mortgage_eur == 0
            assert b.mortgage_ltv_pct == 0
            assert not _has(b, "substitute_tax_mortgage")
            assert not [c for c in _codes(b) if c.startswith("ltv_")]
            assert b.down_payment_eur == 450_000

    def test_the_scenario_exposes_one_normalised_amount(self):
        assert _scenario(mortgage_ltv_pct=80).mortgage_amount_eur == 360_000
        assert _scenario(mortgage_eur=123_456).mortgage_amount_eur == 123_456
        assert _scenario().mortgage_amount_eur == 0

    @pytest.mark.parametrize("ltv", [100.01, 101, 250, -1])
    def test_ltv_outside_0_to_100_is_rejected(self, ltv: float):
        with pytest.raises(ValidationError):
            _scenario(mortgage_ltv_pct=ltv)

    def test_both_amount_and_ltv_are_rejected(self):
        with pytest.raises(ValidationError, match="not both"):
            _scenario(mortgage_eur=300_000, mortgage_ltv_pct=80)
        with pytest.raises(ValidationError, match="not both"):
            _scenario(mortgage_eur=300_000, mortgage_ltv_pct=0)

    def test_an_untouched_amount_does_not_conflict_with_an_ltv(self):
        # mortgage_eur defaults to 0: "not given", so a client that sends it can still use LTV.
        assert _scenario(mortgage_eur=0, mortgage_ltv_pct=80).mortgage_amount_eur == 360_000


class TestCashSplit:
    @pytest.mark.parametrize(
        "kw",
        [
            {},
            {"mortgage_eur": 300_000},
            {"mortgage_ltv_pct": 80, "surface_m2": 70, "renovation_tier": RenovationTier.MEDIUM},
            {"mortgage_ltv_pct": 100, "rendita_eur": None},
        ],
    )
    def test_down_payment_plus_costs_is_the_cash_needed(self, kw):
        b = compute(_scenario(**kw))
        assert b.down_payment_eur == b.price_eur - b.mortgage_eur
        for part in ("low_eur", "central_eur", "high_eur"):
            assert b.down_payment_eur + getattr(b.total_on_top, part) == pytest.approx(
                getattr(b.cash_needed, part)
            )

    def test_worked_split_for_the_80_percent_loan(self):
        b = compute(_scenario(mortgage_ltv_pct=80))
        assert b.down_payment_eur == 90_000
        assert b.cash_needed.central_eur == 90_000 + b.total_on_top.central_eur

    def test_mortgage_lines_are_part_of_the_costs_not_of_the_down_payment(self):
        with_loan = compute(_scenario(mortgage_ltv_pct=80))
        without = compute(_scenario())
        assert with_loan.total_on_top.central_eur > without.total_on_top.central_eur
        assert without.down_payment_eur == 450_000


class TestLtvNotes:
    def test_above_80_percent_the_limit_note_appears(self):
        b = compute(_scenario(mortgage_ltv_pct=85))
        note = _note(b, "ltv_above_limit")
        assert note.params == {"ltv_pct": 85, "limit_pct": 80, "max_pct": 100}
        assert note.source_url == rates.BANCA_ITALIA_CIRC_229_URL

    @pytest.mark.parametrize(
        "kw", [{"mortgage_ltv_pct": 80}, {"mortgage_eur": 360_000}, {"mortgage_ltv_pct": 50}]
    )
    def test_at_or_below_80_percent_there_is_no_limit_note(self, kw):
        assert "ltv_above_limit" not in _codes(compute(_scenario(**kw)))

    def test_just_above_80_percent_triggers_it(self):
        assert "ltv_above_limit" in _codes(compute(_scenario(mortgage_ltv_pct=80.01)))
        assert "ltv_above_limit" in _codes(compute(_scenario(mortgage_eur=360_050)))

    def test_typing_exactly_80_is_never_read_as_above_80_because_of_rounding(self):
        # 80 % of 100,001 is 80,000.8, which rounds up to 80,001: still "80 %", not above it.
        b = compute(_scenario(price_eur=100_001, mortgage_ltv_pct=80))
        assert b.mortgage_eur == 80_001
        assert "ltv_above_limit" not in _codes(b)

    def test_the_limit_comes_from_rates(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(rates, "MUTUO_FONDIARIO_MAX_LTV_PCT", 70.0)
        assert "ltv_above_limit" in _codes(compute(_scenario(mortgage_ltv_pct=75)))

    def test_the_note_never_computes_a_guarantee_amount(self):
        # Only the rule is stated; what a bank would ask is not worked out.
        note = _note(compute(_scenario(mortgage_ltv_pct=90)), "ltv_above_limit")
        assert set(note.params) == {"ltv_pct", "limit_pct", "max_pct"}

    # --- Consap Fondo Garanzia prima casa -----------------------------------------------

    def _consap(self, **kw) -> bool:
        base = dict(price_eur=290_000, mortgage_ltv_pct=85)  # 246,500 at 85 %
        base.update(kw)
        return "consap_fund" in _codes(compute(_scenario(**base)))

    def test_consap_note_appears_when_every_condition_holds(self):
        b = compute(_scenario(price_eur=290_000, mortgage_ltv_pct=85))
        assert b.mortgage_eur == 246_500
        note = _note(b, "consap_fund")
        assert note.source_url == rates.CONSAP_FUND_URL
        assert note.params == {
            "guarantee_pct": 50,
            "max_loan_eur": 250_000,
            "enhanced_guarantee_pct": 80,
            "disability_from": "2026-08-03",
            "disability_isee_eur": 40_000,
            "isee_eur": 40_000,
            "min_ltv_pct": 80,
            "valid_until": "2027-12-31",
        }

    def test_consap_needs_the_ltv_to_be_above_80_percent(self):
        assert not self._consap(mortgage_ltv_pct=80)
        assert not self._consap(mortgage_ltv_pct=60)

    def test_consap_needs_prima_casa(self):
        assert not self._consap(prima_casa=False)

    @pytest.mark.parametrize(
        "category", [CadastralCategory.A1, CadastralCategory.A8, CadastralCategory.A9]
    )
    def test_consap_excludes_a1_a8_a9(self, category: CadastralCategory):
        assert not self._consap(category=category)

    @pytest.mark.parametrize("category", [CadastralCategory.A2, CadastralCategory.A3])
    def test_consap_allows_the_other_categories(self, category: CadastralCategory):
        assert self._consap(category=category)

    def test_consap_loan_cap_is_inclusive(self):
        assert self._consap(price_eur=300_000, mortgage_ltv_pct=None, mortgage_eur=250_000)
        assert not self._consap(price_eur=300_000, mortgage_ltv_pct=None, mortgage_eur=250_001)

    def test_consap_ends_on_its_expiry_date(self):
        assert self._consap(as_of=date(2027, 12, 31))
        assert not self._consap(as_of=date(2028, 1, 1))
        assert self._consap(as_of=None)  # no clock: the pure calculation shows the rule

    def test_consap_is_only_ever_mentioned_together_with_the_limit_note(self):
        b = compute(_scenario(price_eur=290_000, mortgage_ltv_pct=85))
        assert _codes(b).index("ltv_above_limit") < _codes(b).index("consap_fund")

    # --- 100 % ----------------------------------------------------------------------------

    def test_full_financing_note_at_100_percent(self):
        b = compute(_scenario(mortgage_ltv_pct=100))
        assert _note(b, "ltv_full_financing").params == {"ltv_pct": 100}
        assert "ltv_above_limit" in _codes(b)

    def test_full_financing_note_when_the_amount_equals_the_price(self):
        assert "ltv_full_financing" in _codes(compute(_scenario(mortgage_eur=450_000)))

    @pytest.mark.parametrize("ltv", [99, 99.9, 90])
    def test_no_full_financing_note_below_100_percent(self, ltv: float):
        assert "ltv_full_financing" not in _codes(compute(_scenario(mortgage_ltv_pct=ltv)))

    # --- always, with a mortgage ------------------------------------------------------------

    @pytest.mark.parametrize(
        "kw",
        [
            {"mortgage_ltv_pct": 20},
            {"mortgage_ltv_pct": 80},
            {"mortgage_ltv_pct": 100},
            {"mortgage_eur": 300_000},
        ],
    )
    def test_the_computed_on_the_price_note_is_always_there_with_a_mortgage(self, kw):
        b = compute(_scenario(**kw))
        note = _note(b, "ltv_on_price_not_appraisal")
        assert note.params == {"ltv_pct": b.mortgage_ltv_pct}

    def test_no_mortgage_no_ltv_notes(self):
        assert not [c for c in _codes(compute(_scenario())) if c.startswith(("ltv_", "consap"))]


class TestLtvRulesContract:
    """The constants carry their citations and say what the verified texts say."""

    def test_limits_are_those_of_the_primary_texts(self):
        assert rates.MUTUO_FONDIARIO_MAX_LTV_PCT == 80
        assert rates.MUTUO_FONDIARIO_MAX_LTV_WITH_GUARANTEES_PCT == 100

    def test_consap_facts_are_those_of_the_cited_page(self):
        # https://www.consap.it/fondo-prima-casa/ ("ultimo aggiornamento 30 settembre 2026")
        assert rates.CONSAP_AS_OF == date(2026, 9, 30)
        assert rates.CONSAP_GUARANTEE_PCT == 50
        assert rates.CONSAP_MAX_LOAN_EUR == 250_000
        assert rates.CONSAP_ENHANCED_GUARANTEE_PCT == 80
        assert rates.CONSAP_VALID_UNTIL == date(2027, 12, 31)
        assert rates.CONSAP_DISABILITY_CATEGORIES_FROM == date(2026, 8, 3)

    def test_consap_higher_guarantee_rules_are_the_two_of_the_norms(self):
        # (a) disability categories, DL 66/2026 art. 4-bis c. 2: ISEE <= 40,000, no LTV condition.
        assert rates.CONSAP_DISABILITY_ENHANCED_ISEE_EUR == 40_000
        # (b) the other categories, DL 73/2021 art. 64 c. 3: ISEE <= 40,000 and a loan above 80 %
        # of the price including accessory costs.
        assert rates.CONSAP_ENHANCED_ISEE_EUR == 40_000
        assert rates.CONSAP_ENHANCED_MIN_LTV_PCT == 80

    def test_consap_large_family_isee_figures_are_access_limits_by_children(self):
        """40k / 45k / 50k are who may access the fund (3 / 4 / 5+ children under 21), not the
        thresholds of the higher guarantee."""
        assert rates.CONSAP_LARGE_FAMILY_ACCESS_ISEE_EUR == (
            (3, 40_000),
            (4, 45_000),
            (5, 50_000),
        )
        assert not hasattr(rates, "CONSAP_ENHANCED_ISEE_LARGE_FAMILY_EUR")
        note = _note(compute(_scenario(price_eur=290_000, mortgage_ltv_pct=85)), "consap_fund")
        assert not [k for k in note.params if "large" in k or "famil" in k]

    def test_consap_norm_sources_are_on_normattiva(self):
        assert rates.CONSAP_DL73_ART64_URL.startswith("https://www.normattiva.it/")
        assert "decreto.legge:2021-05-25;73~art64" in rates.CONSAP_DL73_ART64_URL
        assert rates.CONSAP_DL66_ART4BIS_URL.startswith("https://www.normattiva.it/")
        assert "decreto.legge:2026-05-07;66~art4bis" in rates.CONSAP_DL66_ART4BIS_URL
        assert rates.CONSAP_EXCLUDED_CATEGORIES == {
            CadastralCategory.A1,
            CadastralCategory.A8,
            CadastralCategory.A9,
        }

    def test_sources_are_primary_hosts(self):
        assert rates.TUB_ART_38_URL.startswith("https://www.normattiva.it/")
        assert rates.BANCA_ITALIA_CIRC_229_URL.startswith("https://www.bancaditalia.it/")
        assert rates.CONSAP_FUND_URL.startswith("https://www.consap.it/")


# ---------------------------------------------------------------------------
# Review intervals and stale ranges
# ---------------------------------------------------------------------------

# Line codes built from a benchmark, by the review interval (months) of that benchmark.
_SIX_MONTH = {
    "mortgage_instruction_fee",
    "mortgage_appraisal",
    "fire_insurance",
    "renovation_works",
    "renovation_contingency",
    "renovation_fees",
}
_TWELVE_MONTH = {
    "notary_purchase",
    "notary_mortgage",
    "agency_fee",
    "conformity_check",
    "moving_utilities",
}
_REVIEWED = date(2026, 10, 6)  # as_of of every benchmark today
_DUE_SIX = date(2027, 4, 6)
_DUE_TWELVE = date(2027, 10, 6)


def _full(**kw) -> PurchaseScenario:
    """Every benchmark-backed line at once: mortgage, medium renovation, default agency fee."""
    return _scenario(
        mortgage_eur=300_000, surface_m2=70, renovation_tier=RenovationTier.MEDIUM, **kw
    )


def _stale(b: CostBreakdown) -> set[str]:
    return {ln.code for ln in b.lines if ln.stale}


class TestReviewIntervals:
    def test_due_is_computed_from_the_interval(self):
        b = rates.Benchmark(1, 2, 3, "https://example.org", as_of=_REVIEWED, review_months=6)
        assert b.due == _DUE_SIX
        assert replace(b, review_months=12).due == _DUE_TWELVE
        assert replace(b, as_of=date(2026, 12, 15)).due == date(2027, 6, 15)

    @pytest.mark.parametrize(
        ("start", "months", "expected"),
        [
            (date(2026, 8, 31), 6, date(2027, 2, 28)),  # clamped to a shorter month
            (date(2027, 8, 31), 6, date(2028, 2, 29)),  # ... including a leap February
            (date(2026, 1, 31), 1, date(2026, 2, 28)),
            (date(2026, 12, 1), 1, date(2027, 1, 1)),  # year rollover
            (date(2026, 10, 6), 24, date(2028, 10, 6)),
        ],
    )
    def test_add_months(self, start, months, expected):
        assert rates.add_months(start, months) == expected

    def test_overdue_starts_the_day_after_due(self):
        b = rates.Benchmark(1, 2, 3, "https://example.org", as_of=_REVIEWED, review_months=6)
        assert not b.overdue(_REVIEWED)
        assert not b.overdue(_DUE_SIX)
        assert b.overdue(_DUE_SIX + timedelta(days=1))

    def test_a_benchmark_must_state_its_review_date_and_interval(self):
        with pytest.raises(TypeError):
            rates.Benchmark(1, 2, 3, "https://example.org")
        with pytest.raises(ValueError, match="review_months"):
            rates.Benchmark(1, 2, 3, "https://example.org", as_of=_REVIEWED, review_months=0)

    def test_cadences(self):
        months = {i.name: i.review_months for i in rates.review_schedule()}
        assert months == {
            # The statutory block: no interval, one reminder due each 15 January.
            "STATUTORY_AS_OF": None,
            # Banca d'Italia market averages: a new issue every month, so a quarterly look.
            "MORTGAGE_RATE_AVERAGE": 3,
            "MORTGAGE_RATE_INITIAL_UP_TO_1Y": 3,
            "MORTGAGE_RATE_INITIAL_OVER_1Y": 3,
            "MORTGAGE_TAEG_AVERAGE": 3,
            "MORTGAGE_INSTRUCTION_FEE": 6,
            "MORTGAGE_APPRAISAL": 6,
            "MORTGAGE_FIRE_INSURANCE": 6,
            "RENOVATION_TIERS": 6,
            "RENOVATION_TECHNICAL_FEES": 6,
            "RENOVATION_CONTINGENCY_PCT": 6,
            "NOTARY_PURCHASE_BANDS[<= 370,000]": 12,
            "NOTARY_PURCHASE_BANDS[<= 465,000]": 12,
            "NOTARY_MORTGAGE_BANDS[<= 370,000]": 12,
            "AGENCY_COMMISSION_PCT": 12,
            "CONFORMITY_CHECK": 12,
            "MOVING_AND_UTILITIES": 12,
        }

    def test_the_schedule_never_lists_a_statutory_figure(self):
        names = {i.name for i in rates.review_schedule()}
        assert not {n for n in names if n.startswith(("REGISTRO", "IMPOSTA", "MUTUO", "CONSAP"))}
        assert "BONUS_RISTRUTTURAZIONE" not in names

    def test_the_schedule_follows_the_constants(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            rates, "MORTGAGE_APPRAISAL", replace(rates.MORTGAGE_APPRAISAL, review_months=3)
        )
        item = next(i for i in rates.review_schedule() if i.name == "MORTGAGE_APPRAISAL")
        assert item.review_months == 3 and item.due == date(2027, 1, 6)

    def test_every_figure_is_in_date_on_the_day_of_the_latest_review(self):
        # The data is only ever added fresh: nothing is born overdue, at any date but today's.
        for item in rates.review_schedule():
            assert item.as_of <= rates.RATES_AS_OF
            assert not item.overdue(rates.RATES_AS_OF), item.name


class TestRatesAsOf:
    def test_it_is_the_latest_review_date_of_any_figure(self):
        latest = max(
            rates.STATUTORY_AS_OF,
            *(i.as_of for i in rates.review_schedule()),
            *(r.as_of for r in rates.BONUS_RISTRUTTURAZIONE.values()),
        )
        assert rates.RATES_AS_OF == latest

    def test_re_reviewing_one_range_advances_it(self, monkeypatch: pytest.MonkeyPatch):
        later = rates.RATES_AS_OF + timedelta(days=40)
        monkeypatch.setattr(
            rates, "MORTGAGE_APPRAISAL", replace(rates.MORTGAGE_APPRAISAL, as_of=later)
        )
        assert rates.latest_review_date() == later

    def test_re_verifying_the_statutory_figures_advances_it(self, monkeypatch: pytest.MonkeyPatch):
        later = rates.RATES_AS_OF + timedelta(days=40)
        monkeypatch.setattr(rates, "STATUTORY_AS_OF", later)
        assert rates.latest_review_date() == later

    def test_an_older_review_does_not_move_it_back(self, monkeypatch: pytest.MonkeyPatch):
        older = rates.RATES_AS_OF - timedelta(days=200)
        monkeypatch.setattr(rates, "CONFORMITY_CHECK", replace(rates.CONFORMITY_CHECK, as_of=older))
        assert rates.latest_review_date() == rates.RATES_AS_OF

    def test_the_breakdown_reports_it(self):
        assert compute(_full()).rates_as_of == rates.RATES_AS_OF

    def test_the_statutory_reminder_does_not_feed_it(self, monkeypatch: pytest.MonkeyPatch):
        # The reminder's due date (15 January) is in the future, and never a review date.
        item = rates.statutory_review()
        assert item.as_of == rates.STATUTORY_AS_OF and item.due > rates.RATES_AS_OF
        assert rates.RATES_AS_OF == max(
            rates.STATUTORY_AS_OF,
            *(i.as_of for i in rates.review_schedule() if not i.statutory),
            *(r.as_of for r in rates.BONUS_RISTRUTTURAZIONE.values()),
        )
        monkeypatch.setattr(rates, "STATUTORY_AS_OF", date(2026, 12, 31))
        assert rates.latest_review_date() == date(2026, 12, 31)  # its as_of, not 2027-01-15


class TestStatutoryReview:
    """The annual reminder to re-verify the statutory block after each budget law.

    A notification for the maintainer (scripts/check_rates.py): it never touches a cost line.
    """

    @pytest.mark.parametrize(
        ("verified", "due"),
        [
            (date(2026, 10, 6), date(2027, 1, 15)),  # the usual case: autumn, due next January
            (date(2026, 12, 31), date(2027, 1, 15)),  # year rollover: the day before the law
            (date(2027, 1, 1), date(2028, 1, 15)),  # verified after the law took effect
            (date(2027, 1, 15), date(2028, 1, 15)),  # on the due date: next year's
            (date(2027, 1, 14), date(2028, 1, 15)),  # verified before the 15th: still next year's
            (date(2028, 2, 29), date(2029, 1, 15)),  # leap day
        ],
    )
    def test_it_is_due_on_15_january_of_the_following_year(self, verified, due):
        assert rates.statutory_review_due(verified) == due
        assert rates.ReviewItem("X", verified, None, None, False, statutory=True).due == due

    def test_it_is_in_the_schedule_and_follows_statutory_as_of(self, monkeypatch):
        (item,) = [i for i in rates.review_schedule() if i.statutory]
        assert item.name == "STATUTORY_AS_OF"
        assert item.as_of == rates.STATUTORY_AS_OF == date(2026, 10, 6)
        assert item.due == date(2027, 1, 15)
        assert item.review_months is None
        assert item.source_url == rates.AGENZIA_ENTRATE_GUIDE_URL and not item.indicative
        monkeypatch.setattr(rates, "STATUTORY_AS_OF", date(2027, 1, 20))
        (item,) = [i for i in rates.review_schedule() if i.statutory]
        assert item.as_of == date(2027, 1, 20) and item.due == date(2028, 1, 15)

    def test_it_is_the_only_statutory_item_and_the_constants_are_not_listed(self):
        assert [i.name for i in rates.review_schedule() if i.statutory] == ["STATUTORY_AS_OF"]
        # ``name`` is the constant to edit: it exists.
        assert hasattr(rates, rates.statutory_review().name)

    def test_overdue_starts_the_day_after_the_due_date(self):
        item = rates.statutory_review()
        assert not item.overdue(date(2026, 12, 31))
        assert not item.overdue(date(2027, 1, 15))
        assert item.overdue(date(2027, 1, 16))

    def test_a_statutory_item_has_no_interval_and_a_benchmark_needs_one(self):
        with pytest.raises(ValueError, match="January"):
            rates.ReviewItem("X", date(2026, 10, 6), 6, None, False, statutory=True)
        for months in (None, 0, -1):
            with pytest.raises(ValueError, match="review_months"):
                rates.ReviewItem("X", date(2026, 10, 6), months, None, False)

    @pytest.mark.parametrize(
        "today",
        [
            date(2026, 10, 8),  # in date
            date(2027, 1, 15),  # due today
            date(2027, 1, 16),  # overdue
            date(2030, 6, 1),  # long overdue
        ],
    )
    def test_no_cost_line_becomes_stale_because_of_it(self, today, monkeypatch):
        """Staleness comes from the benchmarks only: with the reminder overdue by decades and
        again with it in date, exactly the same lines are stale, and no statutory line is."""
        scenario = _full(as_of=today)
        baseline = compute(scenario)
        monkeypatch.setattr(rates, "STATUTORY_AS_OF", date(2000, 1, 1))  # reminder long overdue
        assert rates.statutory_review().overdue(today)
        reminded = compute(scenario)
        key = lambda b: [(ln.code, ln.stale, ln.due) for ln in b.lines]  # noqa: E731
        assert key(reminded) == key(baseline)
        assert [n for n in reminded.notes if n.code == "ranges_overdue"] == [
            n for n in baseline.notes if n.code == "ranges_overdue"
        ]
        for ln in reminded.lines:
            if ln.status is CostStatus.STATUTORY:
                assert ln.stale is False and ln.due is None, ln.code

    def test_the_overdue_note_never_counts_it(self):
        # 2027-01-16: the reminder (01-15) and the reference rates (01-09) are overdue; only
        # ranges that back a line can be counted, and the 6-month ones are not due until April.
        b = compute(_full(as_of=date(2027, 1, 16)))
        assert rates.statutory_review().overdue(date(2027, 1, 16))
        assert not _stale(b) and "ranges_overdue" not in _codes(b)


class TestStaleRanges:
    def test_each_benchmark_line_carries_its_due_date(self):
        b = compute(_full())
        for ln in b.lines:
            if ln.code in _SIX_MONTH:
                assert ln.due == rates.add_months(ln.as_of, 6), ln.code
            elif ln.code in _TWELVE_MONTH:
                assert ln.due == rates.add_months(ln.as_of, 12), ln.code
            else:
                assert ln.due is None, ln.code

    def test_nothing_is_stale_before_the_due_date(self):
        b = compute(_full(as_of=_DUE_SIX))  # due today is still in time
        assert _stale(b) == set()
        assert "ranges_overdue" not in _codes(b)

    def test_six_month_ranges_go_stale_the_day_after_they_are_due(self):
        b = compute(_full(as_of=_DUE_SIX + timedelta(days=1)))
        assert _stale(b) == _SIX_MONTH

    def test_twelve_month_ranges_follow_six_months_later(self):
        assert _stale(compute(_full(as_of=_DUE_TWELVE))) == _SIX_MONTH
        b = compute(_full(as_of=_DUE_TWELVE + timedelta(days=1)))
        assert _stale(b) == _SIX_MONTH | _TWELVE_MONTH

    def test_without_a_clock_nothing_is_flagged(self):
        b = compute(_full(as_of=None))
        assert _stale(b) == set()
        assert "ranges_overdue" not in _codes(b)
        assert all(ln.due is not None for ln in b.lines if ln.code in _SIX_MONTH | _TWELVE_MONTH)

    @pytest.mark.parametrize(
        "kw",
        [
            {},
            {"seller": SellerType.BUILDER_VAT},
            {"prima_casa": False},
            {"rendita_eur": None},  # registro bounded by the missing rendita: needs_input
            {"category": CadastralCategory.A1},
        ],
    )
    def test_statutory_lines_are_never_stale(self, kw):
        far_future = date(2027, 10, 7)  # every benchmark overdue, bonus year 2027 still shown
        b = compute(_full(as_of=far_future, **kw))
        statutory = [ln for ln in b.lines if ln.status is CostStatus.STATUTORY]
        assert statutory, "scenario must exercise statutory lines"
        for ln in statutory:
            assert ln.stale is False and ln.due is None, ln.code
        # Lines whose rate is statutory and whose base is flagged elsewhere carry no flag.
        for code in ("renovation_vat", "renovation_bonus", "registro_tax", "vat_builder"):
            for ln in (ln for ln in b.lines if ln.code == code):
                assert ln.stale is False and ln.due is None, code
        assert _has(b, "renovation_bonus")

    def test_stale_is_separate_from_status(self):
        fresh = compute(_full(as_of=date(2026, 10, 8)))
        stale = compute(_full(as_of=date(2027, 10, 7)))
        status = lambda b: {  # noqa: E731
            ln.code: ln.status for ln in b.lines if ln.group is not CostGroup.OFFSET
        }
        assert status(fresh) == status(stale)
        # Both at once: indicative and stale.
        works = _line(stale, "renovation_works")
        assert works.status is CostStatus.INDICATIVE and works.stale
        # A plain benchmark can be stale too.
        assert _line(stale, "mortgage_appraisal").status is CostStatus.BENCHMARK
        assert _line(stale, "mortgage_appraisal").stale

    def test_staleness_never_moves_a_figure(self):
        fresh = compute(_full(as_of=date(2026, 10, 8)))
        stale = compute(_full(as_of=date(2027, 10, 7)))
        assert (fresh.closing, fresh.renovation) == (stale.closing, stale.renovation)
        assert fresh.total_on_top == stale.total_on_top
        assert fresh.cash_needed == stale.cash_needed

    def test_one_note_counts_the_overdue_ranges_and_gives_their_review_date(self):
        b = compute(_full(as_of=_DUE_SIX + timedelta(days=1)))
        notes = [n for n in b.notes if n.code == "ranges_overdue"]
        assert len(notes) == 1
        assert notes[0].params == {"count": len(_SIX_MONTH), "last_reviewed": "2026-10-06"}
        assert compute(_full(as_of=_DUE_TWELVE + timedelta(days=1))).notes[-1].params["count"] == (
            len(_SIX_MONTH) + len(_TWELVE_MONTH)
        )

    def test_the_note_names_the_oldest_review_date(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            rates, "MORTGAGE_APPRAISAL", replace(rates.MORTGAGE_APPRAISAL, as_of=date(2026, 12, 1))
        )
        b = compute(_full(as_of=date(2027, 6, 2)))  # past both 6-month dues
        assert "mortgage_appraisal" in _stale(b)
        (note,) = [n for n in b.notes if n.code == "ranges_overdue"]
        assert note.params["last_reviewed"] == "2026-10-06"

    def test_a_range_reviewed_later_is_not_dragged_along(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            rates, "MORTGAGE_APPRAISAL", replace(rates.MORTGAGE_APPRAISAL, as_of=date(2026, 12, 1))
        )
        b = compute(_full(as_of=date(2027, 4, 7)))  # appraisal not due until 2027-06-01
        assert "mortgage_appraisal" not in _stale(b)
        assert "mortgage_instruction_fee" in _stale(b)

    def test_a_chosen_agency_percentage_is_not_ours_to_flag(self):
        late = date(2027, 10, 7)
        assert _line(compute(_scenario(as_of=late)), "agency_fee").stale  # default
        assert _line(compute(_scenario(agency_pct=3.0, as_of=late)), "agency_fee").stale
        own = _line(compute(_scenario(agency_pct=2.5, as_of=late)), "agency_fee")
        assert not own.stale and own.due is None

    def test_no_note_and_no_flag_when_all_ranges_are_in_date(self):
        b = compute(_full(as_of=date(2026, 10, 8)))
        assert not _stale(b) and "ranges_overdue" not in _codes(b)

    def test_compute_stays_pure(self):
        s = _full(as_of=date(2027, 10, 7))
        assert compute(s) == compute(s)


# ---------------------------------------------------------------------------
# Mortgage payment: French amortisation, interest and the cost of the loan
# ---------------------------------------------------------------------------

_MORTGAGE_LINE_CODES = (
    "substitute_tax_mortgage",
    "notary_mortgage",
    "mortgage_instruction_fee",
    "mortgage_appraisal",
    "fire_insurance",
)


def _with_rate(rate=3.0, years=25, mortgage=360_000, **kw) -> PurchaseScenario:
    return _scenario(mortgage_eur=mortgage, mortgage_years=years, mortgage_rate_pct=rate, **kw)


class TestMonthlyPayment:
    def test_the_standard_formula_on_the_reference_case(self):
        # 360,000 over 25 years at a 3.00 % TAN. The exact French-amortisation instalment is
        # 1,707.1607..., i.e. EUR 1,707.16 to the cent. (The brief quoted 1,707.17: one cent
        # above the exact value, which is what rounding the instalment *up* would give; the
        # standard formula rounds half-up, so 1,707.16 is what stimmo shows.)
        from stimmo.costs.calculator import monthly_payment

        assert monthly_payment(360_000, 3.0, 25) == pytest.approx(1_707.1607, abs=1e-4)
        payment = compute(_with_rate()).mortgage_payment
        assert payment.monthly_payment_eur == 1_707.16
        assert payment.monthly_payment_eur == pytest.approx(1_707.17, abs=0.011)
        assert (payment.years, payment.months, payment.rate_pct) == (25, 300, 3.0)

    def test_the_formula_is_the_closed_form_not_an_approximation(self):
        # P · i / (1 − (1 + i)^−n), checked against an independent month-by-month schedule.
        from stimmo.costs.calculator import monthly_payment

        principal, i, n = 250_000.0, 0.042 / 12, 20 * 12
        instalment = monthly_payment(principal, 4.2, 20)
        balance = principal
        for _ in range(n):
            balance = balance * (1 + i) - instalment
        assert balance == pytest.approx(0, abs=1e-6)

    def test_zero_rate_is_the_loan_divided_by_the_months(self):
        b = compute(_with_rate(rate=0.0, years=20, mortgage=300_000))
        p = b.mortgage_payment
        assert p.monthly_payment_eur == 1_250.0  # 300,000 / 240
        assert p.total_interest_eur == 0.0
        assert p.total_repaid_eur == 300_000.0
        assert p.total_cost.central_eur == p.mortgage_costs.central_eur  # no interest to add

    def test_zero_rate_never_shows_negative_interest_from_rounding(self):
        # 100,000 / 300 = 333.333...: rounding the instalment first would repay 99,999.
        p = compute(_with_rate(rate=0.0, mortgage=100_000)).mortgage_payment
        assert p.monthly_payment_eur == 333.33
        assert p.total_repaid_eur == 100_000.0
        assert p.total_interest_eur == 0.0

    def test_totals(self):
        b = compute(_with_rate())
        p = b.mortgage_payment
        # Repaid is the exact instalment x months; interest is what is repaid beyond the loan.
        assert p.total_repaid_eur == pytest.approx(1_707.160729887 * 300, abs=0.005)
        assert p.total_interest_eur == pytest.approx(p.total_repaid_eur - 360_000, abs=0.005)
        assert p.total_interest_eur == 152_148.22
        # The instalment x months and the repaid total agree to within the rounding of the cents.
        assert abs(p.monthly_payment_eur * p.months - p.total_repaid_eur) < p.months * 0.005 + 0.01

    def test_total_cost_is_interest_plus_the_mortgage_costs_already_computed(self):
        b = compute(_with_rate())
        p = b.mortgage_payment
        lines = [_line(b, code) for code in _MORTGAGE_LINE_CODES]
        for end in ("low", "central", "high"):
            costs = sum(getattr(ln, f"{end}_eur") for ln in lines)
            assert getattr(p.mortgage_costs, f"{end}_eur") == pytest.approx(costs, abs=0.01)
            assert getattr(p.total_cost, f"{end}_eur") == pytest.approx(
                p.total_interest_eur + costs, abs=0.01
            )
        assert p.total_cost.low_eur <= p.total_cost.central_eur <= p.total_cost.high_eur

    def test_the_mortgage_costs_are_the_ones_in_the_closing_costs_not_new_ones(self):
        with_rate = compute(_with_rate())
        without = compute(_scenario(mortgage_eur=360_000))
        # The payment adds no cost line and moves no total: it is a view of the loan.
        keep = {"mortgage_payment", "notes"}
        assert with_rate.model_dump(exclude=keep) == without.model_dump(exclude=keep)

    def test_the_term_defaults_to_25_years(self):
        default = compute(_scenario(mortgage_eur=360_000, mortgage_rate_pct=3.0)).mortgage_payment
        assert default.years == rates.MORTGAGE_DEFAULT_YEARS == 25
        assert default == compute(_with_rate(years=25)).mortgage_payment

    @pytest.mark.parametrize("years", rates.MORTGAGE_YEARS_OPTIONS)
    def test_every_offered_term_is_an_exact_amortisation(self, years):
        p = compute(_with_rate(years=years)).mortgage_payment
        assert p.months == years * 12
        assert p.total_repaid_eur == pytest.approx(p.monthly_payment_eur * p.months, abs=2)

    def test_longer_term_lowers_the_payment_and_raises_the_interest(self):
        short = compute(_with_rate(years=10)).mortgage_payment
        long = compute(_with_rate(years=30)).mortgage_payment
        assert long.monthly_payment_eur < short.monthly_payment_eur
        assert long.total_interest_eur > short.total_interest_eur

    def test_a_percentage_mortgage_gives_the_same_payment_as_the_amount(self):
        by_ltv = compute(_scenario(mortgage_ltv_pct=80, mortgage_rate_pct=3.0))
        assert by_ltv.mortgage_payment == compute(_with_rate()).mortgage_payment

    def test_the_variable_rate_note_comes_with_every_payment(self):
        b = compute(_with_rate(rate=3.25, years=20))
        (note,) = [n for n in b.notes if n.code == "mortgage_variable_rate"]
        assert note.params == {"rate_pct": 3.25, "years": 20}
        assert "mortgage_rate_missing" not in _codes(b)

    def test_no_mortgage_no_payment(self):
        for s in (_scenario(), _scenario(mortgage_rate_pct=3.0, mortgage_years=25)):
            b = compute(s)
            assert b.mortgage_payment is None
            assert not [n for n in b.notes if n.code.startswith("mortgage_")]

    def test_a_rate_is_never_guessed(self):
        b = compute(_scenario(mortgage_eur=300_000))
        assert b.mortgage_payment is None
        (note,) = [n for n in b.notes if n.code == "mortgage_rate_missing"]
        # A pointer to the market reference, not a default: the payment is still absent.
        assert note.params["reference_pct"] == rates.MORTGAGE_RATE_AVERAGE.central
        assert note.params["data_month"] == rates.MORTGAGE_RATE_DATA_MONTH.isoformat()
        assert note.source_url == rates.MORTGAGE_RATE_SOURCE_URL
        assert "mortgage_variable_rate" not in _codes(b)

    @pytest.mark.parametrize(
        "kw", [{"mortgage_rate_pct": -0.1}, {"mortgage_rate_pct": 20.5}, {"mortgage_years": 0},
               {"mortgage_years": 41}]
    )  # fmt: skip
    def test_out_of_range_rate_or_term_is_rejected(self, kw):
        with pytest.raises(ValidationError):
            _scenario(mortgage_eur=300_000, **kw)

    def test_the_payment_says_nothing_about_the_buyer(self):
        # The legal boundary, pinned: a generic calculation, no affordability or income notion.
        from stimmo.models import MortgagePayment

        assert set(MortgagePayment.model_fields) == {
            "years",
            "months",
            "rate_pct",
            "monthly_payment_eur",
            "total_interest_eur",
            "total_repaid_eur",
            "mortgage_costs",
            "total_cost",
        }
        forbidden = ("afford", "income", "reddito", "ratio", "eligib", "sustain")
        names = {*MortgagePayment.model_fields, *PurchaseScenario.model_fields}
        assert not [n for n in names if any(f in n for f in forbidden)]

    def test_compute_stays_pure(self):
        s = _with_rate(as_of=date(2027, 10, 7))
        assert compute(s) == compute(s)


class TestReferenceRates:
    NAMES = (
        "MORTGAGE_RATE_AVERAGE",
        "MORTGAGE_RATE_INITIAL_UP_TO_1Y",
        "MORTGAGE_RATE_INITIAL_OVER_1Y",
        "MORTGAGE_TAEG_AVERAGE",
    )

    @pytest.mark.parametrize("name", NAMES)
    def test_each_is_a_benchmark_reviewed_every_three_months_from_the_primary_source(self, name):
        b = getattr(rates, name)
        assert isinstance(b, rates.Benchmark)
        assert b.review_months == 3
        assert b.as_of <= rates.RATES_AS_OF
        assert b.source_url.startswith("https://www.bancaditalia.it/")
        assert not b.indicative  # a published official figure
        assert b.low == b.central == b.high  # one published number, not a range

    @pytest.mark.parametrize("name", NAMES)
    def test_the_freshness_mechanism_covers_each_one(self, name):
        item = next(i for i in rates.review_schedule() if i.name == name)
        assert item.review_months == 3
        assert item.due == rates.add_months(item.as_of, 3)
        assert not item.overdue(item.due) and item.overdue(item.due + timedelta(days=1))

    def test_the_data_month_is_a_first_of_month_not_after_the_review(self):
        month = rates.MORTGAGE_RATE_DATA_MONTH
        assert month.day == 1
        assert month <= rates.MORTGAGE_RATE_AVERAGE.as_of

    def test_the_taeg_is_not_a_tan_and_is_never_the_default_rate(self):
        assert rates.MORTGAGE_RATE_DEFAULT_PCT == rates.MORTGAGE_RATE_AVERAGE.central
        assert rates.MORTGAGE_RATE_DEFAULT_PCT != rates.MORTGAGE_TAEG_AVERAGE.central
        # Fees and insurance make the TAEG the higher figure; a sanity check on the numbers.
        assert rates.MORTGAGE_TAEG_AVERAGE.central > rates.MORTGAGE_RATE_AVERAGE.central

    def test_the_overall_average_sits_between_the_two_initial_period_averages(self):
        lo = rates.MORTGAGE_RATE_INITIAL_UP_TO_1Y.central
        hi = rates.MORTGAGE_RATE_INITIAL_OVER_1Y.central
        assert lo <= rates.MORTGAGE_RATE_AVERAGE.central <= hi

    def test_the_year_options_are_the_documented_ones(self):
        assert rates.MORTGAGE_YEARS_OPTIONS == (10, 15, 20, 25, 30)
        assert rates.MORTGAGE_DEFAULT_YEARS in rates.MORTGAGE_YEARS_OPTIONS
