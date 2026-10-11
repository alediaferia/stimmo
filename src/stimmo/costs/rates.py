"""Transaction-cost rates and benchmarks — the tuning surface of the purchase-cost calculator.

This is a declared *second* tuning surface, next to ``valuation/adjustments.py``. The two never
mix: ``adjustments.py`` tunes the price estimate (hedonic multipliers on the OMI band); this
module holds what buying costs *on top of* the price — statutory taxes, conventional fees and
renovation €/m². Nothing here feeds ``engine.estimate()`` or the verdict.

Rules for this file:

* every constant carries a comment citing the law or source it comes from;
* every benchmark (a range, as opposed to a statute) carries ``as_of``, ``source_url`` and a
  review interval (``review_months``); past ``as_of`` + interval the benchmark is *overdue* and
  every line built from it renders as "to be checked" (see ``review_schedule()`` and
  ``scripts/check_rates.py``);
* a figure with no public source, or one not confirmed current, is ``indicative=True`` and is
  always rendered as an indicative range, never as a firm number;
* a figure that is not backed by a cited source is left out rather than guessed.

Re-verify the tax figures after the 2027 budget law and again at year end. Four testi unici
take effect on 2027-01-01 and repeal the provisions they consolidate: D.Lgs. 117/2026 (imposte sui
redditi), D.Lgs. 10/2026 (IVA), D.Lgs. 123/2025 (registro) and D.Lgs. 141/2026 (adempimenti e
accertamento). The rules used below read the same in the new texts, renumbered, so no figure
changes; each comment cites the provision in force until 2026-12-31 and, after "from 2027", its
successor. The 2027 renovation-bonus rates are provisional until the 2027 budget law is passed.
Statutory figures have no review interval and are never "to be checked" on a line: they change by
law, so they are tracked by that re-verification (``STATUTORY_AS_OF``). The schedule carries one
annual reminder for it, due on 15 January of the year after ``STATUTORY_AS_OF``
(``statutory_review()``): it only notifies the maintainer.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from stimmo.models import CadastralCategory, FineCondition, RenovationTier

# Date the statutory figures below (taxes, LTV rules, renovation bonus) were last verified against
# their sources. Bump it after each budget law. Statutory figures carry no review interval, but the
# review schedule reminds the maintainer to re-verify them each January (statutory_review()).
# RATES_AS_OF, the latest of this and every benchmark's own review date, is defined at the bottom.
STATUTORY_AS_OF = date(2026, 10, 6)

# Primary source for the purchase taxes below: Agenzia delle Entrate, "Guida per l'acquisto
# della casa" (October 2025).
AGENZIA_ENTRATE_GUIDE_URL = (
    "https://www.agenziaentrate.gov.it/portale/documents/d/guest/"
    "guida_acquisto_casa_le_imposte_e_le_agevolazioni"
)


def add_months(day: date, months: int) -> date:
    """``day`` moved forward by whole calendar months, clamped to the end of a shorter month
    (31 August + 6 months is 28/29 February, not an error)."""
    year, month0 = divmod(day.year * 12 + day.month - 1 + months, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Benchmark:
    """A published range: ``low`` and ``high`` bound it, ``central`` is the figure budgeted.

    ``central`` is the value used for the headline total; it is never a new estimate — it is
    either a figure stated alongside the range in the cited material, or the range midpoint.

    ``as_of`` is the date the range was last checked against its source and ``review_months``
    how long it may be trusted after that. Both are required: a benchmark never inherits a
    review date, so re-reviewing one range moves exactly that range's date.
    """

    low: float
    central: float
    high: float
    source_url: str | None
    # True when the range is not confirmed current, or no public source is cited for it.
    indicative: bool = False
    as_of: date = field(kw_only=True)
    review_months: int = field(kw_only=True)

    def __post_init__(self) -> None:
        if not self.low <= self.central <= self.high:
            raise ValueError(f"benchmark must be ordered, got {self}")
        # An unsourced figure may never masquerade as a firm benchmark.
        if self.source_url is None and not self.indicative:
            raise ValueError("a benchmark without a source_url must be indicative")
        if self.review_months <= 0:
            raise ValueError("review_months must be positive")

    @property
    def due(self) -> date:
        """The date the range is next due for review: ``as_of`` plus the interval."""
        return add_months(self.as_of, self.review_months)

    def overdue(self, today: date) -> bool:
        """True from the day after ``due``: a range reviewed on the due date is still in time."""
        return today > self.due


# Budget laws take effect on 1 January (and are passed in late December), so the statutory figures
# are due for re-verification on 15 January of the year after STATUTORY_AS_OF: two weeks to read
# what was actually enacted.
STATUTORY_REVIEW_MONTH = 1
STATUTORY_REVIEW_DAY = 15


def statutory_review_due(as_of: date) -> date:
    """The next budget-law re-verification for statutory figures last verified on ``as_of``: 15
    January of the following year (verified 2026-10-06 is due 2027-01-15; so is 2026-12-31)."""
    return date(as_of.year + 1, STATUTORY_REVIEW_MONTH, STATUTORY_REVIEW_DAY)


@dataclass(frozen=True)
class ReviewItem:
    """One row of the review schedule: a figure and when it is next due.

    ``name`` is the rates.py constant to edit when the figure is re-reviewed.

    A ``statutory`` item is the annual reminder for the statutory block, not a benchmark: it has
    no ``review_months`` and is due on 15 January of the year after ``as_of``
    (``statutory_review_due``). It is a notification only. No cost line is ever derived from it, so
    it can never mark one "to be checked" (statutory lines are never stale).
    """

    name: str
    as_of: date
    review_months: int | None
    source_url: str | None
    indicative: bool
    statutory: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        if self.statutory:
            if self.review_months is not None:
                raise ValueError("a statutory review item is due each January, not every N months")
        elif self.review_months is None or self.review_months <= 0:
            raise ValueError("review_months must be positive")

    @classmethod
    def of(cls, name: str, b: Benchmark) -> ReviewItem:
        return cls(name, b.as_of, b.review_months, b.source_url, b.indicative)

    @property
    def due(self) -> date:
        if self.statutory:
            return statutory_review_due(self.as_of)
        assert self.review_months is not None  # __post_init__
        return add_months(self.as_of, self.review_months)

    def overdue(self, today: date) -> bool:
        return today > self.due


# ---------------------------------------------------------------------------
# Purchase taxes — statutory
# Statutory constants (these, the LTV rules and the renovation bonus) have no review interval:
# they change by law, so they are re-verified after each budget law (STATUTORY_AS_OF); the review
# schedule reminds the maintainer each January (statutory_review()).
# ---------------------------------------------------------------------------

# Imposta di registro on a purchase from a private (or otherwise non-IVA) seller:
# 2 % prima casa, 9 % otherwise, never less than the minimum per deed.
# Statute: DPR 131/1986, Tariffa parte I; from 2027 D.Lgs. 123/2025 (TU registro), Tariffa parte I
# artt. 1 and 11.
# Source: AGENZIA_ENTRATE_GUIDE_URL.
REGISTRO_RATE_PRIMA_CASA = 0.02
REGISTRO_RATE_OTHER = 0.09
REGISTRO_MIN_EUR = 1_000.0

# "Prezzo-valore": on request in the deed, the registro base is the cadastral value rather than
# the price — rendita catastale × 1.05 × 110 (prima casa) or × 120 (other). Without the request
# the base is the declared price, which is therefore the ceiling used when the rendita is
# unknown. Statute: L. 266/2005 art. 1 c. 497; from 2027 D.Lgs. 141/2026 (TU adempimenti e
# accertamento) art. 299 cc. 5 and 10. Source: AGENZIA_ENTRATE_GUIDE_URL.
CADASTRAL_REVALUATION = 1.05
CADASTRAL_MULTIPLIER_PRIMA_CASA = 110
CADASTRAL_MULTIPLIER_OTHER = 120

# Imposta ipotecaria + imposta catastale, same case: €50 + €50 (bollo, tasse ipotecarie and
# tributi speciali are exempt). Source: AGENZIA_ENTRATE_GUIDE_URL.
IPOTECARIA_EUR = 50.0
CATASTALE_EUR = 50.0

# Purchase from a builder selling under IVA: IVA on the price (4 % prima casa, 10 % otherwise,
# 22 % for A/1, A/8, A/9) plus registro, ipotecaria and catastale at a fixed €200 each. No
# prezzo-valore on this path. Source: AGENZIA_ENTRATE_GUIDE_URL.
BUILDER_IVA_PRIMA_CASA = 0.04
BUILDER_IVA_OTHER = 0.10
BUILDER_IVA_EXCLUDED_CATEGORY = 0.22
BUILDER_REGISTRO_EUR = 200.0
BUILDER_IPOTECARIA_EUR = 200.0
BUILDER_CATASTALE_EUR = 200.0

# A/1, A/8 and A/9 are excluded from the prima-casa regime (registro, IVA and imposta
# sostitutiva all fall back to the ordinary rates). Source: AGENZIA_ENTRATE_GUIDE_URL.
PRIMA_CASA_EXCLUDED_CATEGORIES: frozenset[CadastralCategory] = frozenset(
    {CadastralCategory.A1, CadastralCategory.A8, CadastralCategory.A9}
)

# Preliminary contract (compromesso): €200 registro plus bollo at €16 per 4 pages. Two stamps
# (up to 8 pages) are assumed. The 0.5 % of caparra paid on registration is credited against
# the final registro since 2025 (D.Lgs. 139/2024), so the caparra is not a cost and is not
# modelled. Source: AGENZIA_ENTRATE_GUIDE_URL.
PRELIMINARY_REGISTRO_EUR = 200.0
PRELIMINARY_BOLLO_EUR = 16.0
PRELIMINARY_BOLLO_STAMPS = 2

# Imposta sostitutiva on the mortgage: 0.25 % prima casa, 2 % otherwise, on the loan amount.
# Source: AGENZIA_ENTRATE_GUIDE_URL.
IMPOSTA_SOSTITUTIVA_PRIMA_CASA = 0.0025
IMPOSTA_SOSTITUTIVA_OTHER = 0.02

# Tassa ipotecaria added to the mortgage-deed notary fee. Source: notaionline.it, see
# NOTARY_MORTGAGE_BANDS.
TASSA_IPOTECARIA_MORTGAGE_EUR = 35.0

# ---------------------------------------------------------------------------
# Mortgage loan-to-value (LTV) rules — general rules, stated as information
#
# Verified 2026-10-07 against the primary texts below. These constants only feed the notes the
# calculator attaches to a scenario: they say what the rules are and never decide whether a
# particular buyer qualifies for anything.
# ---------------------------------------------------------------------------

# Mutuo fondiario (credito fondiario) limit.
# Art. 38 TUB (D.Lgs. 385/1993) defines credito fondiario (c. 1: medium- and long-term loans
# secured by a first-ranking mortgage on real estate) and, in c. 2, has the Banca d'Italia, "in
# conformità delle deliberazioni del CICR", set the maximum amount of the loan relative to "il
# valore dei beni ipotecati o il costo delle opere da eseguire sugli stessi".
# https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:1993-09-01;385~art38
TUB_ART_38_URL = (
    "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:1993-09-01;385~art38"
)
# The numbers come from the CICR deliberation of 22 April 1995, which Banca d'Italia implements in
# its Istruzioni di Vigilanza per le banche (Circolare 229/1999), Titolo V, Capitolo 1, Sezione II,
# par. 1 "Limiti di finanziabilità" (annotated edition, updated to March 2026):
#   * 80 % of "il valore dei beni immobili ipotecati o del costo delle opere da eseguire sugli
#     stessi, ivi compreso il costo dell'area o dell'immobile da ristrutturare";
#   * "può essere elevato fino al 100 per cento in presenza di garanzie integrative offerte dal
#     cliente" — fideiussioni bancarie, polizze fideiussorie di compagnie di assicurazione,
#     garanzia di fondi pubblici di garanzia o di consorzi e cooperative di garanzia fidi, and a
#     few others (cessioni di crediti verso lo Stato, pegno su titoli di Stato, ...);
#   * the guarantees must be at least such that loan / (value of the property + guarantees)
#     does not exceed 80 %. That is stricter than "guarantees for the share above 80 %": a 90 %
#     loan needs guarantees worth 12.5 % of the value, not 10 %. The notes state the rule as
#     written and never compute a guarantee amount.
# https://www.bancaditalia.it/compiti/vigilanza/normativa/archivio-norme/circolari/c229/CIRC_229_annotata.pdf
# The text of the CICR deliberation itself was not read (no primary copy was found online);
# the Banca d'Italia text above states the same limits and cites the deliberation as its basis.
# The texts say "valore dei beni immobili ipotecati". That the bank measures it on its own
# appraisal (perizia), so the ratio on the price is only an approximation, is the product
# premise and market practice; none of the texts read says it.
BANCA_ITALIA_CIRC_229_URL = (
    "https://www.bancaditalia.it/compiti/vigilanza/normativa/archivio-norme/circolari/c229/"
    "CIRC_229_annotata.pdf"
)
MUTUO_FONDIARIO_MAX_LTV_PCT = 80.0
MUTUO_FONDIARIO_MAX_LTV_WITH_GUARANTEES_PCT = 100.0

# Fondo di garanzia per la prima casa (Consap): https://www.consap.it/fondo-prima-casa/
# Read as raw text on 2026-10-09; the page itself is dated "ultimo aggiornamento 30 settembre
# 2026" (``CONSAP_AS_OF``). Statutory, so no review interval: re-read it with the budget laws.
# Only these facts are encoded or stated by the notes:
#   * the guarantee is reserved to priority categories ("l'accesso al Fondo è riservato ai
#     soggetti appartenenti alle seguenti categorie", L. 147/2013 art. 1 c. 48 lett. c, as
#     amended): giovani coppie (household formed at least two years ago, one member under 36),
#     nuclei monogenitoriali con figli minori, conduttori di alloggi IACP, giovani under 36 and
#     large families (below). From 3 August 2026 also persons with a permanent disability under
#     art. 3 c. 3 L. 104/1992 and members of a household that has included such a person (child
#     or sibling of the applicant) for at least two years (L. 2 luglio 2026 n. 116, converting
#     DL 7 maggio 2026 n. 66, art. 4-bis c. 1: 30 days after the law's entry into force on
#     4 July 2026);
#   * ordinary guarantee: 50 % of the capital share (L. 147/2013 art. 1 c. 48 lett. c, "nella
#     misura massima del 50 per cento della quota capitale"); the loan may not exceed €250,000;
#   * the applicant must not own other residential property at the date of the application,
#     abroad included (except property inherited and given in free use to parents or siblings);
#   * the home must become the borrower's abitazione principale, must not be A/1, A/8 or A/9
#     and must not have the "caratteristiche di lusso" of DM 2 agosto 1969 n. 1072 (the last
#     condition is stated by the notes but not computable, so it is never checked here);
#   * higher guarantee, two SEPARATE rules, both 80 % of the capital share:
#       (a) for the disability categories: ISEE at most €40,000, with no loan-to-price condition
#           (DL 66/2026 art. 4-bis c. 2, as converted by L. 116/2026: CONSAP_DL66_ART4BIS_URL);
#       (b) for the other priority categories: ISEE at most €40,000 and a "limite di
#           finanziabilità", the ratio of the loan to "il prezzo d'acquisto dell'immobile,
#           comprensivo degli oneri accessori", above 80 % (DL 25 maggio 2021 n. 73 art. 64 c. 3,
#           first period, as converted by L. 106/2021; the Consap page says only "nel rispetto
#           dei limiti ISEE previsti", the figure is that of the norm: CONSAP_DL73_ART64_URL,
#           consolidated text last updated 2026-02-28). Applications up to 31/12/2027, after
#           the extensions of the term, the last being L. 30 dicembre 2024 n. 207 art. 1 c. 112;
#   * the €40,000 / €45,000 / €50,000 ISEE figures for 3 / 4 / 5+ children under 21 are ACCESS
#     limits for large families (the page's list of categories), NOT thresholds for the higher
#     guarantee: those are rules (a) and (b) above.
# The calculator measures the loan against the price alone, while rule (b) measures it against
# the price plus accessory costs. The loan-to-price ratio is never lower than the loan-to-(price
# + costs) one, so the note is shown whenever the rule could apply and sometimes when it does
# not: the note states the rule as written and sends the reader to Consap and the bank.
CONSAP_FUND_URL = "https://www.consap.it/fondo-prima-casa/"
CONSAP_DL73_ART64_URL = (
    "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legge:2021-05-25;73~art64"
)
CONSAP_DL66_ART4BIS_URL = (
    "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legge:2026-05-07;66~art4bis"
)
CONSAP_AS_OF = date(2026, 9, 30)  # "ultimo aggiornamento" of the page above
CONSAP_DISABILITY_CATEGORIES_FROM = date(2026, 8, 3)  # DL 66/2026 art. 4-bis c. 1, see above
CONSAP_GUARANTEE_PCT = 50.0
CONSAP_MAX_LOAN_EUR = 250_000.0
CONSAP_ENHANCED_GUARANTEE_PCT = 80.0
CONSAP_DISABILITY_ENHANCED_ISEE_EUR = 40_000.0  # rule (a)
CONSAP_ENHANCED_ISEE_EUR = 40_000.0  # rule (b)
CONSAP_ENHANCED_MIN_LTV_PCT = 80.0  # rule (b): the loan must exceed this share of price + costs
# Access limits for large families: number of children under 21 → maximum ISEE (the last entry
# is "five or more"). Not thresholds for the higher guarantee.
CONSAP_LARGE_FAMILY_ACCESS_ISEE_EUR: tuple[tuple[int, float], ...] = (
    (3, 40_000.0),
    (4, 45_000.0),
    (5, 50_000.0),
)
# Last day on which an application can still use the higher guarantee of rule (b).
CONSAP_VALID_UNTIL = date(2027, 12, 31)
# Same three categories as PRIMA_CASA_EXCLUDED_CATEGORIES, kept as its own constant because the
# two rules have different legal bases.
CONSAP_EXCLUDED_CATEGORIES: frozenset[CadastralCategory] = frozenset(
    {CadastralCategory.A1, CadastralCategory.A8, CadastralCategory.A9}
)

# ---------------------------------------------------------------------------
# Professional fees — conventional (negotiable)
# ---------------------------------------------------------------------------

# Ordinary IVA, charged on agency and notary fees (agency "+ IVA 22 %").
IVA_ORDINARY = 0.22

# Agency: 2–3 % + IVA per side in practice, 3 % common on the buyer's side. Owed on the
# conclusion of the deal (binding accepted proposal or preliminary), not at the deed
# (art. 1755 c.c.; only REA-registered agents can claim, L. 39/1989). The percentage is a
# user input; the default is the common buyer-side figure and the low case is the bottom of
# the 2–3 % range. No public source is cited → indicative; the benchmark carries the review date
# of the default, which is what goes stale when the buyer does not enter their own percentage.
AGENCY_COMMISSION_PCT = Benchmark(
    2.0, 3.0, 3.0, None, indicative=True, as_of=date(2026, 10, 6), review_months=12
)
AGENCY_DEFAULT_PCT = AGENCY_COMMISSION_PCT.central
AGENCY_LOW_PCT = AGENCY_COMMISSION_PCT.low


@dataclass(frozen=True)
class PriceBand:
    """A benchmark valid up to and including ``max_eur`` of price (or loan) amount."""

    max_eur: float
    benchmark: Benchmark


# Notary fee for the purchase deed, ex IVA. Fees are free-market since DL 1/2012 and
# negotiable. ⚠ not confirmed current → indicative. The bands stop at €465,000: above that no
# band is published, so the calculator reuses the top band and says so.
# Source: https://studiomadera.it/news/237-costo-notaio
# Central: ~€2,500 is the typical quote given alongside the band for a €450k purchase; the
# ≤ €370k band has no typical quote published, so its low end is budgeted.
_NOTARY_PURCHASE_URL = "https://studiomadera.it/news/237-costo-notaio"
NOTARY_PURCHASE_BANDS: tuple[PriceBand, ...] = (
    PriceBand(
        370_000,
        Benchmark(
            2_250,
            2_250,
            3_360,
            _NOTARY_PURCHASE_URL,
            indicative=True,
            as_of=date(2026, 10, 6),
            review_months=12,
        ),
    ),
    PriceBand(
        465_000,
        Benchmark(
            2_480,
            2_500,
            3_730,
            _NOTARY_PURCHASE_URL,
            indicative=True,
            as_of=date(2026, 10, 6),
            review_months=12,
        ),
    ),
)

# Notary fee for the mortgage deed, ex IVA; the tassa ipotecaria (above) is added on top.
# ⚠ not confirmed current → indicative. The band is published for loans up to €370,000.
# Source: https://notaionline.it/guida/costo-notaio-mutuo/
_NOTARY_MORTGAGE_URL = "https://notaionline.it/guida/costo-notaio-mutuo/"
NOTARY_MORTGAGE_BANDS: tuple[PriceBand, ...] = (
    PriceBand(
        370_000,
        Benchmark(
            1_750,
            2_000,
            2_300,
            _NOTARY_MORTGAGE_URL,
            indicative=True,
            as_of=date(2026, 10, 6),
            review_months=12,
        ),
    ),
)

# ---------------------------------------------------------------------------
# Mortgage extras — benchmark ranges
# Source: https://www.facile.it/mutui/news/spese-accessorie-mutuo-quali-sono-come-risparmiare.html
# ---------------------------------------------------------------------------
_MORTGAGE_EXTRAS_URL = (
    "https://www.facile.it/mutui/news/spese-accessorie-mutuo-quali-sono-come-risparmiare.html"
)
# Reviewed every 6 months (banks reprice these fees often).
MORTGAGE_INSTRUCTION_FEE = Benchmark(  # istruttoria
    0, 600, 1_500, _MORTGAGE_EXTRAS_URL, as_of=date(2026, 10, 6), review_months=6
)
MORTGAGE_APPRAISAL = Benchmark(  # perizia
    0, 300, 400, _MORTGAGE_EXTRAS_URL, as_of=date(2026, 10, 6), review_months=6
)
# Fire-and-explosion (scoppio-incendio) cover is the only mandatory policy; the buyer may bring
# their own (DL 1/2012 art. 28). Single-premium range; ~€40–150/yr is the annual alternative.
MORTGAGE_FIRE_INSURANCE = Benchmark(
    300, 500, 1_000, _MORTGAGE_EXTRAS_URL, as_of=date(2026, 10, 6), review_months=6
)

# ---------------------------------------------------------------------------
# Mortgage payment — French amortisation and market reference rates
#
# The payment is a generic calculation on the amount, term and rate the user chooses (see
# calculator.py `_mortgage_payment`): constant monthly instalments of principal and interest at a
# constant rate. It never looks at the buyer — no income, no affordability test, no lender.
# ---------------------------------------------------------------------------

# Terms offered by the form (years), and the one a mortgage gets when none is chosen. Round
# terms only: the calculator itself accepts any whole number of years the scenario allows.
MORTGAGE_YEARS_OPTIONS: tuple[int, ...] = (10, 15, 20, 25, 30)
MORTGAGE_DEFAULT_YEARS = 25
MONTHS_PER_YEAR = 12

# Reference rates shown beside the rate field, so a visitor has an official market average to
# start from. Source: Banca d'Italia, "Banche e moneta: serie nazionali", Tavola 2.4 (MIR0400),
# "Tassi di interesse bancari sui prestiti in euro alle famiglie: nuove operazioni", block
# "Prestiti per l'acquisto di abitazioni" (page 25 of the July 2026 issue, published 2026-09-09).
# The table is the harmonised euro-area MIR survey, a sample of Italian banks scaled to the whole
# system; Banca d'Italia's own guide ("I tassi di interesse bancari. Guida ai dati pubblicati
# dalla Banca d'Italia", 2023-03-03) says it collects *nominal* rates — the rate agreed between
# lender and borrower net of other charges, which is what the TAN field asks for — and, for house
# purchase and consumer credit only, the TAEG as well. The table therefore carries, for new loans:
#   * the average nominal rate over all of them;
#   * the same split by initial rate-setting period ("fino a 1 anno" / "oltre 1 anno"). Banca
#     d'Italia does not publish a fixed/variable split as such: a rate set for up to a year at
#     the outset is in practice a variable-rate loan, one set for longer a fixed- or mixed-rate
#     one, and the calculator says "typically" and no more;
#   * the TAEG, a different measure (interest plus fees and mandatory insurance). It is shown as
#     the TAEG, never as a TAN, and never fed to the payment calculation.
# These are market averages over a month of new loans, not an offer and not a rate any lender
# would quote a given buyer. The latest month is flagged provisional by Banca d'Italia
# (figures in parentheses in the table); MORTGAGE_RATE_PROVISIONAL records that.
# Re-read the newest issue every quarter (review_months=3): the index page
# https://www.bancaditalia.it/pubblicazioni/moneta-banche/2026-moneta/index.html lists them
# (the issue with August data is scheduled for 2026-10-09 in Banca d'Italia's 2026 calendar).
MORTGAGE_RATE_SOURCE_URL = (
    "https://www.bancaditalia.it/pubblicazioni/moneta-banche/2026-moneta/"
    "statistiche_BAM_20260909.pdf"
)
# First day of the month the figures below describe (July 2026).
MORTGAGE_RATE_DATA_MONTH = date(2026, 7, 1)
MORTGAGE_RATE_PROVISIONAL = True
# A single published figure, so low = central = high.
MORTGAGE_RATE_AVERAGE = Benchmark(
    3.40, 3.40, 3.40, MORTGAGE_RATE_SOURCE_URL, as_of=date(2026, 10, 9), review_months=3
)
MORTGAGE_RATE_INITIAL_UP_TO_1Y = Benchmark(
    2.99, 2.99, 2.99, MORTGAGE_RATE_SOURCE_URL, as_of=date(2026, 10, 9), review_months=3
)
MORTGAGE_RATE_INITIAL_OVER_1Y = Benchmark(
    3.56, 3.56, 3.56, MORTGAGE_RATE_SOURCE_URL, as_of=date(2026, 10, 9), review_months=3
)
MORTGAGE_TAEG_AVERAGE = Benchmark(
    3.81, 3.81, 3.81, MORTGAGE_RATE_SOURCE_URL, as_of=date(2026, 10, 9), review_months=3
)
# What a mortgage's rate field starts at: the all-loans average, shown and editable.
MORTGAGE_RATE_DEFAULT_PCT = MORTGAGE_RATE_AVERAGE.central

# ---------------------------------------------------------------------------
# Other one-off costs — no public source cited → indicative
# ---------------------------------------------------------------------------

# Conformity check (cadastral and planning) before signing. A cadastral mismatch voids the
# deed (L. 52/1985 art. 29) and a planning irregularity is the buyer's risk (Cass. SU
# 8230/2019), so the check is worth budgeting. Remedies if it finds something (late CILA about
# €1,000, sanatoria art. 36-bis €1,032–10,328, DOCFA €400–800) are not included.
CONFORMITY_CHECK = Benchmark(
    400, 600, 1_000, None, indicative=True, as_of=date(2026, 10, 6), review_months=12
)

# Moving plus utility activation. Moving (bilocale) €600–1,500 plus suolo pubblico if needed;
# utilities €50–250 ⚠.
MOVING_AND_UTILITIES = Benchmark(
    650, 1_150, 1_750, None, indicative=True, as_of=date(2026, 10, 6), review_months=12
)

# ---------------------------------------------------------------------------
# Renovation — Milan 2026. The whole table is ⚠ → indicative.
#
# Every tier bound below is the one printed in a single source: the "Fascia orientativa" table of
# https://www.ghle.it/post/quanto-costa-ristrutturare-casa-milano-2026 (re-read 2026-10-09):
#   LIGHT    ← "Rinnovamento leggero"             250–500 €/m²
#   MEDIUM   ← "Ristrutturazione parziale"        600–1.000 €/m²
#   FULL     ← "Ristrutturazione completa"        1.000–1.500 €/m²  (the page's own worked
#              example agrees: 80 m² ≈ €80,000–120,000)
#   PREMIUM  ← "Intervento complesso o di pregio" 1.600–2.500 €/m² "e oltre" (open-ended)
# The page leaves a gap between 1.500 and 1.600; it is kept, not bridged. (FULL's upper bound
# was 1,600 here until 2026-10-09: that was the premium tier's lower bound, not the source's.)
#
# OPEN QUESTION — IVA basis. The page never says whether these €/m² include IVA. It lists "IVA"
# as its own line of a complete budget and among the items a quote may leave out, which reads as
# net of IVA but is not a statement about the bands. The Regione Lombardia Prezzario dei lavori
# pubblici 2026 (the list the page itself cites as a technical reference) says in its reading
# guide "I prezzi sono sempre indicati al netto dell'I.V.A.": that fixes the convention of public
# price lists, not the basis of these bands. No source found states it, so the calculator ASSUMES
# ex IVA (and adds RENOVATION_IVA on top), says so on the page and keeps the table indicative.
# If the bands turn out to include IVA, the works are overstated by about RENOVATION_IVA.
# Authoritative unit-price lists (CCIAA Milano "Prezzi informativi", DEI) are paywalled; the
# Regione Lombardia list is free.
# ---------------------------------------------------------------------------
_RENOVATION_URL = "https://www.ghle.it/post/quanto-costa-ristrutturare-casa-milano-2026"
# Cross-check on the IVA convention only (Parte 1, "Guida alla lettura del prezzario 2026"); no
# figure of the calculator comes from it.
RENOVATION_IVA_CROSSCHECK_URL = (
    "https://www.regione.lombardia.it/content/dam/rl/canali-tematici-servizi/"
    "09-infrastrutture-trasporti-e-mobilità/01-acquisti-e-contratti-pubblici/"
    "02-prezzario-regionale-dei-lavori-pubblici/02-prezzario-regionale-dei-lavori-pubblici-infr/"
    "allegati/allegato-2026-unico/allegati-corretti/"
    "all-parte-1-elenco-prezzi-civile-urbanizzazione-difesa-suolo-agroforestale.pdf"
)


@dataclass(frozen=True)
class RenovationTierRate:
    low_eur_m2: float
    high_eur_m2: float
    open_ended: bool = False  # the published range is "high and above"

    @property
    def central_eur_m2(self) -> float:
        return (self.low_eur_m2 + self.high_eur_m2) / 2


RENOVATION_TIERS: dict[RenovationTier, RenovationTierRate] = {
    RenovationTier.LIGHT: RenovationTierRate(250, 500),
    RenovationTier.MEDIUM: RenovationTierRate(600, 1_000),
    RenovationTier.FULL: RenovationTierRate(1_000, 1_500),
    RenovationTier.PREMIUM: RenovationTierRate(1_600, 2_500, open_ended=True),
}
RENOVATION_SOURCE_URL = _RENOVATION_URL
# The €/m² table is one reviewed unit (it is not a Benchmark: it holds a range per tier). Reviewed
# every 6 months, the cadence of the half-yearly Milan chamber-of-commerce price list that
# authoritatively backs these figures.
RENOVATION_TIERS_REVIEW = ReviewItem(
    name="RENOVATION_TIERS",
    as_of=date(2026, 10, 6),
    review_months=6,
    source_url=_RENOVATION_URL,
    indicative=True,
)
RENOVATION_AS_OF = RENOVATION_TIERS_REVIEW.as_of

# IVA on renovation works: 10 % (L. 488/1999 art. 7 c. 1 lett. b, with DM 29/12/1999 for the "beni
# significativi"); from 2027 D.Lgs. 10/2026 (TU IVA) art. 35 c. 4 and Tabella A parte IV n. 121,
# which still refers to the same DM (art. 169 lett. zz).
# Source: AGENZIA_ENTRATE_RENOVATION_GUIDE_URL, §2.
# Applied flat to works plus contingency. The rule is more granular and the calculator can only
# understate it (the ex-IVA assumption on the €/m² bands, below, is a separate matter). On ordinary
# and extraordinary maintenance, beni significativi supplied under the contract (lifts, windows and
# doors, boilers, video door-entry systems, air conditioning, sanitaryware and bathroom taps,
# security systems) are at 10 % only up to the value of the contract net of those goods; the excess
# is at the ordinary rate, which also applies to materials the client buys directly, goods supplied
# by someone else, professional services and subcontracts invoiced to the contractor. Restauro,
# risanamento conservativo and ristrutturazione edilizia are at 10 % in full. Modelling it needs
# the value of the beni significativi and the regime of the works, neither of which exists in the
# input, and a default split would be an invented coefficient: it is disclosed instead (the
# renovation_vat_partial note).
RENOVATION_IVA = 0.10
# Agenzia delle Entrate, "Ristrutturazioni edilizie: le agevolazioni fiscali" (October 2025), §2
# "L'agevolazione IVA": the 10 % and the beni-significativi limit.
AGENZIA_ENTRATE_RENOVATION_GUIDE_URL = (
    "https://www.agenziaentrate.gov.it/portale/documents/d/guest/"
    "ristrutturazioni_edilizie_le_agevolazioni_fiscali"
)

# Technical fees and filings, €3–6k, central €4,000, taken as ALL-IN (IVA included): professional
# services carry IVA at the ordinary rate (not the 10 % of the works), but the range is an unsourced
# placeholder for the technician's quote, so no IVA is added on top of it.
# NO PUBLIC SOURCE, so source_url is None and
# the range is indicative (a Benchmark without a source must be). It used to cite the ghle.it page
# above, which gives the CILA's €75 diritti di segreteria (Comune di Milano, from 2026-05-01) but
# no figure for the professional's fee. Searched 2026-10-09 for a published fee survey for a
# CILA-based renovation in Milan and found none: fees are free-market since 2012, and the pages
# found give percentages of the works (design ~4 %, works supervision 4–8 %, a studio's own
# rates) or forum anecdotes, not a survey. Deriving a range from them would be a new model, not a
# sourced figure. Replace the range with a sourced one, or drop it, when one turns up.
RENOVATION_TECHNICAL_FEES = Benchmark(
    3_000, 4_000, 6_000, None, indicative=True, as_of=date(2026, 10, 6), review_months=6
)

# The technical fee applies from the medium tier up. Light works (paint, overlay floors,
# internal doors) are manutenzione ordinaria, i.e. edilizia libera: no CILA and no technician
# are needed (DPR 380/2001 art. 6; Glossario edilizia libera, DM 2 marzo 2018), so the light
# tier carries no technical fee.
RENOVATION_TIERS_WITH_TECHNICAL_FEES: frozenset[RenovationTier] = frozenset(
    {RenovationTier.MEDIUM, RenovationTier.FULL, RenovationTier.PREMIUM}
)

# Contingency on the works, in percent: 8–12 %, midpoint 10 %. Applied to the works and
# carries IVA like them.
RENOVATION_CONTINGENCY_PCT = Benchmark(
    8, 10, 12, _RENOVATION_URL, indicative=True, as_of=date(2026, 10, 6), review_months=6
)

# Default renovation tier suggested for a listing condition. Deterministic and only a starting
# point — the user can always pick another tier. "Da ristrutturare" listings conventionally
# mean a full renovation (cf. the "Needs full renovation" label in mcp/resources.py); the
# other three conditions are treated as needing no works until the user says otherwise.
# This does NOT feed the valuation: the condition delta there is FINE_CONDITION_DELTA in
# valuation/adjustments.py, calibrated independently.
FINE_CONDITION_DEFAULT_TIER: dict[FineCondition, RenovationTier] = {
    FineCondition.NUOVO: RenovationTier.NONE,
    FineCondition.RISTRUTTURATO: RenovationTier.NONE,
    FineCondition.ABITABILE: RenovationTier.NONE,
    FineCondition.DA_RISTRUTTURARE: RenovationTier.FULL,
}


def default_renovation_tier(condition: FineCondition) -> RenovationTier:
    return FINE_CONDITION_DEFAULT_TIER[condition]


# ---------------------------------------------------------------------------
# Bonus ristrutturazione — personal-income-tax deduction on renovation spend
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BonusRule:
    """Deduction rates by the calendar year the works are paid (bank-transfer year)."""

    main_residence_pct: float
    other_pct: float
    # True when the rates are what current law provides for that year but a budget law
    # still to be passed may change them.
    provisional: bool
    source_url: str
    as_of: date = STATUTORY_AS_OF


# The statute itself, as in force on 2026-12-31: DL 63/2013 art. 16 c. 1 as amended by L. 199/2025
# states the rates of both years (50 / 36 % in 2026, 36 / 30 % in 2027) and the spend cap. The
# link is pinned to that date (!vig=) so it keeps showing this text after the testo unico repeals
# the article on 2027-01-01. Re-point it at the act that sets the 2027 rates once the 2027 budget
# law is known.
BONUS_SOURCE_URL = (
    "https://www.normattiva.it/uri-res/N2Ls?"
    "urn:nir:stato:decreto.legge:2013-06-04;63~art16!vig=2026-12-31"
)

# 2026: 50 % abitazione principale / 36 % other. 2027 under current law: 36 % / 30 %. Statute: DL
# 63/2013 art. 16 c. 1 as amended by L. 199/2025; from 2027 D.Lgs. 117/2026 (TU imposte sui redditi)
# art. 373. A selective 50 % extension of the 2027 rates is possible but unfunded ⚠, so that year
# is provisional and is labelled as subject to the 2027 budget law.
BONUS_RISTRUTTURAZIONE: dict[int, BonusRule] = {
    2026: BonusRule(50, 36, provisional=False, source_url=BONUS_SOURCE_URL),
    2027: BonusRule(36, 30, provisional=True, source_url=BONUS_SOURCE_URL),
}
# Spend cap per unit, deducted in equal annual instalments over 10 years. Statute: DL 63/2013
# art. 16 c. 1 as amended by L. 199/2025; from 2027 D.Lgs. 117/2026 art. 373. The cap on total
# deductions for incomes above €75k (art. 16-ter TUIR; art. 18 D.Lgs. 117/2026 from 2027) is not
# modelled: it depends on the buyer's income.
BONUS_SPEND_CAP_EUR = 96_000.0
BONUS_INSTALMENT_YEARS = 10

# Tiers whose works qualify for the bonus. On a single dwelling, art. 16-bis c.1 TUIR (art. 17 c.1
# D.Lgs. 117/2026 from 2027, same letters) covers manutenzione ordinaria only on condominium
# common parts (lett. a); the unit itself qualifies from manutenzione straordinaria upwards
# (lett. b) — see AGENZIA_ENTRATE_RENOVATION_GUIDE_URL. Light works (paint, overlay floors,
# internal doors) are ordinaria, so the light tier carries no bonus on its own (it can become
# eligible as part of a larger straordinaria intervention, which is a different tier).
# Same tier set as RENOVATION_TIERS_WITH_TECHNICAL_FEES, kept as its own constant because the
# two rules have different legal bases.
BONUS_ELIGIBLE_TIERS: frozenset[RenovationTier] = frozenset(
    {RenovationTier.MEDIUM, RenovationTier.FULL, RenovationTier.PREMIUM}
)


# ---------------------------------------------------------------------------
# Review schedule — what is overdue, and the date the page's content last changed
# ---------------------------------------------------------------------------


def statutory_review() -> ReviewItem:
    """The annual reminder to re-verify the statutory block (taxes, LTV rules, Consap, bonus).

    Built from ``STATUTORY_AS_OF`` on each call, so it follows the constant and cannot drift from
    it. Re-verify the figures against the new budget law, then bump ``STATUTORY_AS_OF``. A
    notification for the maintainer only: nothing in the calculator reads it, so no line is
    flagged stale because of it.
    """
    return ReviewItem(
        "STATUTORY_AS_OF",
        STATUTORY_AS_OF,
        None,
        AGENZIA_ENTRATE_GUIDE_URL,
        False,
        statutory=True,
    )


def review_schedule() -> tuple[ReviewItem, ...]:
    """Every figure in this module with a review date, in file order.

    Discovered from the module's own constants — every ``Benchmark``, every band of a
    ``PriceBand`` table and every ``ReviewItem`` — so a figure added above cannot be left out of
    the schedule. The statutory constants themselves have no interval and are not listed; the one
    ``statutory_review()`` reminder stands for all of them.
    """
    items: list[ReviewItem] = [statutory_review()]
    for name, value in list(globals().items()):
        if isinstance(value, Benchmark):
            items.append(ReviewItem.of(name, value))
        elif isinstance(value, ReviewItem):
            items.append(value)
        elif isinstance(value, tuple) and value and all(isinstance(v, PriceBand) for v in value):
            items.extend(
                ReviewItem.of(f"{name}[<= {band.max_eur:,.0f}]", band.benchmark) for band in value
            )
    return tuple(items)


def latest_review_date() -> date:
    """The most recent review of any figure, statutory or not."""
    return max(
        STATUTORY_AS_OF,
        *(item.as_of for item in review_schedule()),
        *(rule.as_of for rule in BONUS_RISTRUTTURAZIONE.values()),
    )


# Date of the most recent review of any figure above. Derived, so it advances whenever any
# benchmark's ``as_of`` (or ``STATUTORY_AS_OF``) is bumped and cannot drift from them. Also the
# <lastmod> of the calculator page: the page's content changes exactly when a figure is re-reviewed.
RATES_AS_OF = latest_review_date()
