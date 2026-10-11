"""Translate AdjustmentBreakdown (code, params) → human-readable string.

The engine emits structured data; this web-layer module renders it for the
user's current locale (read from the active ContextVar in stimmo.i18n).
"""

from __future__ import annotations

from datetime import date

from babel.dates import format_date
from babel.numbers import format_decimal
from markupsafe import Markup

from stimmo.i18n import _current_locale, fmt_eur, use_locale
from stimmo.i18n import gettext as _
from stimmo.models import (
    AdjustmentBreakdown,
    CostLine,
    CostNote,
    CostStatus,
    RenovationTier,
)


def render(b: AdjustmentBreakdown, locale: str | None = None) -> str:
    """Return a translated label for a breakdown entry.

    Pass `locale` explicitly when rendering outside a request context (e.g. tests).
    """
    if locale is not None:
        with use_locale(locale):
            return _render_impl(b)
    return _render_impl(b)


def _render_impl(b: AdjustmentBreakdown) -> str:
    code = b.code
    p = b.params

    match code:
        case "floor":
            lift_s = _("yes") if p.get("lift") else _("no")
            return _("Floor %(floor)s, lift: %(lift)s") % {
                "floor": p.get("floor", "?"),
                "lift": lift_s,
            }
        case "size":
            return _("Size (%(surface_m2)s m²)") % {
                "surface_m2": _fmt_g(p.get("surface_m2", "?")),
            }
        case "condition_fine":
            return _("Interior condition (%(condition)s)") % {
                "condition": p.get("condition", "?"),
            }
        case "energy_class":
            return _("Energy class %(cls)s") % {"cls": p.get("cls", "?")}
        case "outdoor":
            return _("Outdoor (%(outdoor)s)") % {"outdoor": p.get("outdoor", "?")}
        case "construction_era":
            return _("Construction era (%(era)s)") % {"era": p.get("era", "?")}
        case "orientation":
            return _("Orientation (%(orientation)s)") % {
                "orientation": p.get("orientation", "?"),
            }
        case "exposure":
            return _("Exposure (%(exposure)s)") % {"exposure": p.get("exposure", "?")}
        case "room_density":
            return _("Cramped layout (%(rooms)s locali · %(m2_per_locale)s m²/locale)") % {
                "rooms": p.get("rooms", "?"),
                "m2_per_locale": _fmt_g(p.get("m2_per_locale", "?")),
            }
        case "lift":
            return _("Lift")
        case "second_bathroom":
            return _("Second bathroom")
        case "amenities":
            return _("Amenities (OSM)")
        case "clamp":
            raw_s = f"{p.get('raw', 0):.3f}"
            clamped_s = f"{p.get('clamped', 0):.3f}"
            return _("Clamp (%(raw)s → %(clamped)s)") % {
                "raw": raw_s,
                "clamped": clamped_s,
            }
        case _:
            return code


def _fmt_g(value: object) -> str:
    try:
        f = float(value)  # type: ignore[arg-type]
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Purchase-cost lines (stimmo.costs): the same code + params → text contract.
# ---------------------------------------------------------------------------


def render_cost_line(line: CostLine, locale: str | None = None) -> str:
    """Translated label for a CostLine. Pass `locale` outside a request context (tests)."""
    if locale is not None:
        with use_locale(locale):
            return _render_cost_line_impl(line)
    return _render_cost_line_impl(line)


def render_cost_note(note: CostNote, locale: str | None = None) -> str:
    """Translated caveat for a CostNote."""
    if locale is not None:
        with use_locale(locale):
            return _render_cost_note_impl(note)
    return _render_cost_note_impl(note)


def status_label(status: CostStatus, locale: str | None = None) -> str:
    """Short translated tag saying how firm a cost line is."""
    if locale is not None:
        with use_locale(locale):
            return _status_label_impl(status)
    return _status_label_impl(status)


def stale_label(locale: str | None = None) -> str:
    """Badge for a line whose benchmark is past its review date."""
    if locale is not None:
        with use_locale(locale):
            return _("to be checked")
    return _("to be checked")


def _status_label_impl(status: CostStatus) -> str:
    match status:
        case CostStatus.STATUTORY:
            return _("statutory")
        case CostStatus.CONTRACTUAL:
            return _("as agreed")
        case CostStatus.BENCHMARK:
            return _("typical range")
        case CostStatus.INDICATIVE:
            return _("indicative")
        case CostStatus.NEEDS_INPUT:
            return _("needs your input")


def pct_str(value: object, decimals: int | None = None) -> str:
    """Locale-formatted unsigned percentage, e.g. 0.25 → '0.25%' / '0,25%'.

    ``decimals`` fixes the number of decimals (a published rate: 3.4 → '3.40%'); by default
    trailing zeros are dropped (a rate someone typed: 3.4 → '3.4%').
    """
    fmt = "#,##0.##" if decimals is None else "#,##0." + "0" * decimals
    try:
        return format_decimal(float(value), format=fmt, locale=_current_locale.get()) + "%"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)


def eur_str(value: object) -> str:
    try:
        return fmt_eur(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)


def _render_cost_line_impl(line: CostLine) -> str:
    code = line.code
    p = line.params

    match code:
        case "registro_tax":
            basis = p.get("basis")
            if basis == "cadastral" and p.get("minimum_applied"):
                return _(
                    "Registro tax — per-deed minimum (%(rate)s of the cadastral value is lower)"
                ) % {"rate": pct_str(p.get("rate_pct"))}
            if basis == "cadastral":
                return _("Registro tax — %(rate)s of the cadastral value (%(value)s)") % {
                    "rate": pct_str(p.get("rate_pct")),
                    "value": eur_str(p.get("cadastral_value_eur")),
                }
            if basis == "price_ceiling":
                return _(
                    "Registro tax — depends on the rendita catastale "
                    "(at most %(rate)s of the price)"
                ) % {"rate": pct_str(p.get("rate_pct"))}
            if basis == "price":
                return _("Registro tax — per-deed minimum (%(rate)s of the price is lower)") % {
                    "rate": pct_str(p.get("rate_pct")),
                }
            return _("Registro tax — fixed amount on a purchase under IVA")
        case "ipotecaria_catastale":
            return _("Imposta ipotecaria + catastale (%(each)s each)") % {
                "each": eur_str(p.get("each_eur")),
            }
        case "vat_builder":
            return _("IVA on the price (%(rate)s)") % {"rate": pct_str(p.get("rate_pct"))}
        case "preliminary_contract":
            return _("Preliminary contract — registro and stamp duty")
        case "notary_purchase":
            return _("Notary fee — purchase deed, incl. IVA")
        case "agency_fee":
            return _("Agency fee (%(pct)s + IVA %(vat)s)") % {
                "pct": pct_str(p.get("pct")),
                "vat": pct_str(p.get("vat_pct")),
            }
        case "substitute_tax_mortgage":
            return _("Imposta sostitutiva on the mortgage (%(rate)s)") % {
                "rate": pct_str(p.get("rate_pct")),
            }
        case "notary_mortgage":
            return _("Notary fee — mortgage deed, incl. IVA and tassa ipotecaria")
        case "mortgage_instruction_fee":
            return _("Bank fee — mortgage application (istruttoria)")
        case "mortgage_appraisal":
            return _("Bank valuation (perizia)")
        case "fire_insurance":
            return _("Fire and explosion insurance (scoppio-incendio)")
        case "conformity_check":
            return _("Cadastral and planning conformity check")
        case "moving_utilities":
            return _("Moving and utility activation")
        case "renovation_works":
            tier = RenovationTier(p["tier"]) if "tier" in p else None
            return _(
                "Works — %(tier)s renovation, %(surface)s m² × %(low)s–%(high)s per m² "
                "(Milan prices, assumed to exclude IVA)"
            ) % {
                "tier": tier_short(tier) if tier else "?",
                "surface": _fmt_g(p.get("surface_m2", "?")),
                "low": eur_str(p.get("eur_m2_low", "?")),
                "high": eur_str(p.get("eur_m2_high", "?")) + ("+" if p.get("open_ended") else ""),
            }
        case "renovation_contingency":
            return _("Contingency on the works (%(pct)s)") % {"pct": pct_str(p.get("pct_central"))}
        case "renovation_vat":
            return _("IVA on works and contingency (%(rate)s)") % {
                "rate": pct_str(p.get("rate_pct")),
            }
        case "renovation_fees":
            return _("Technical fees and filings (incl. CILA, IVA included)")
        case "renovation_bonus":
            who = _("main residence") if p.get("main_residence") else _("other homes")
            label = _(
                "Bonus ristrutturazione — works paid in %(year)s: %(rate)s back over "
                "%(years)s years (%(who)s)"
            ) % {
                "year": p.get("year", "?"),
                "rate": pct_str(p.get("rate_pct")),
                "years": p.get("years", "?"),
                "who": who,
            }
            if p.get("provisional"):
                label += " · " + _("subject to the %(year)s budget law") % {
                    "year": p.get("year", "?")
                }
            return label
        case _:
            return code


def _render_cost_note_impl(note: CostNote) -> str:
    p = note.params
    match note.code:
        case "rendita_missing":
            return _(
                "The registro tax depends on the rendita catastale, which listings rarely state. "
                "Until you enter it the figure shown is the most you can owe (%(ceiling)s, if the "
                "tax is charged on the price) and the totals use it; with the rendita it can be "
                "much lower, down to the %(floor)s per-deed minimum."
            ) % {"ceiling": eur_str(p.get("ceiling_eur")), "floor": eur_str(p.get("floor_eur"))}
        case "prima_casa_excluded_category":
            return _(
                "Category %(category)s is excluded from the prima-casa regime, so the ordinary "
                "rates are applied."
            ) % {"category": p.get("category", "?")}
        case "notary_above_published_band":
            # The two notes differ only in the deed they name, so that is what is in bold. The
            # catalog string is the trusted markup; the parameters are escaped by `_markup`.
            if p.get("kind") == "mortgage":
                return _markup(
                    _(
                        "The notary fee we rely on for the <b>mortgage deed</b> is published for "
                        "loans up to %(max)s. The figure shown is for that top band and probably "
                        "understates the fee: ask the notary for a quote."
                    ),
                    max=eur_str(p.get("band_max_eur")),
                )
            return _markup(
                _(
                    "The notary fee we rely on for the <b>purchase deed</b> is published for "
                    "prices up to %(max)s. The figure shown is for that top band and probably "
                    "understates the fee: ask the notary for a quote."
                ),
                max=eur_str(p.get("band_max_eur")),
            )
        case "ltv_above_limit":
            return _(
                "The loan is %(ltv)s of the price. For a mutuo fondiario the ordinary limit is "
                "%(limit)s of the value of the mortgaged property, or of the cost of the works "
                "(art. 38 TUB; CICR deliberation of 22 April 1995, as implemented by the Banca "
                "d'Italia). Above it, the rules allow up to %(max)s only with integrative "
                "guarantees, such as bank or insurance guarantees (fideiussioni) or guarantee "
                "funds, large enough that the loan stays within %(limit)s of the property's "
                "value plus the guarantees. Whether guarantees would be needed in a given case, "
                "and which, is for the bank to say: ask it."
            ) % {
                "ltv": pct_str(p.get("ltv_pct")),
                "limit": pct_str(p.get("limit_pct")),
                "max": pct_str(p.get("max_pct")),
            }
        case "consap_fund":
            return _(
                "The Fondo di garanzia per la prima casa, managed by Consap, guarantees %(pct)s "
                "of the principal of loans of up to %(max_loan)s for a main residence that is "
                "not in categories A/1, A/8 or A/9 and does not have the luxury features of "
                "ministerial decree no. 1072 of 2 August 1969. Access is reserved to "
                "priority categories (young couples, single-parent households with minor "
                "children, tenants of IACP public housing, people under 36, large families "
                "and, from %(from)s, people with a permanent disability and members of "
                "households that include one), and the borrower must not own another home, "
                "including abroad. The guarantee can be raised to %(high)s: for the disability "
                "categories with an ISEE of at most %(isee_disability)s, and for the other "
                "categories on a loan above %(ltv)s of the purchase price including accessory "
                "costs, with an ISEE of at most %(isee)s (applications until %(until)s). "
                "Check Consap's conditions and ask the bank. This is general information, not "
                "an assessment of any buyer."
            ) % {
                "max_loan": eur_str(p.get("max_loan_eur")),
                "pct": pct_str(p.get("guarantee_pct")),
                "high": pct_str(p.get("enhanced_guarantee_pct")),
                "from": date_str(p.get("disability_from")),
                "isee_disability": eur_str(p.get("disability_isee_eur")),
                "isee": eur_str(p.get("isee_eur")),
                "ltv": pct_str(p.get("min_ltv_pct")),
                "until": date_str(p.get("valid_until")),
            }
        case "ltv_full_financing":
            return _(
                "A loan equal to the whole price does not cover the purchase taxes, the notary "
                "and agency fees or the other costs listed here: they are still paid in cash."
            )
        case "ranges_overdue":
            return _(
                "%(count)s of the ranges below are past their scheduled review (last reviewed "
                "%(date)s) and may no longer be current. They are marked “%(label)s”: verify "
                "them against the cited sources."
            ) % {
                "count": p.get("count", "?"),
                "date": date_str(p.get("last_reviewed")),
                "label": _("to be checked"),
            }
        case "mortgage_variable_rate":
            return _(
                "The monthly payment is computed at the %(rate)s rate you entered, kept constant "
                "over %(years)s years. If your rate is variable, this is its value today: the "
                "payment will change as the rate moves."
            ) % {"rate": pct_str(p.get("rate_pct")), "years": p.get("years", "?")}
        case "mortgage_rate_missing":
            return _(
                "Enter an interest rate (TAN) to see the monthly payment and the cost of the "
                "mortgage. For reference, the Banca d'Italia average on new house-purchase loans "
                "was %(rate)s in %(month)s: a market average, not an offer."
            ) % {
                "rate": pct_str(p.get("reference_pct"), 2),
                "month": month_str(p.get("data_month")),
            }
        case "renovation_vat_partial":
            return _(
                "IVA on the works and the contingency is applied at a flat %(rate)s. Part of a "
                "renovation can be at %(ordinary)s: significant goods (windows, boilers, "
                "sanitaryware and similar) get the reduced rate only up to the value of the "
                "contract net of those goods, and materials you buy yourself are at %(ordinary)s. "
                "The IVA shown can therefore be an underestimate."
            ) % {
                "rate": pct_str(p.get("rate_pct")),
                "ordinary": pct_str(p.get("ordinary_rate_pct")),
            }
        case "ltv_on_price_not_appraisal":
            return _(
                "The loan-to-value ratio (%(ltv)s) is computed on the price. The bank applies "
                "its limits to the value it assigns the property in its own appraisal (perizia), "
                "which can be lower than the price, so the real ratio may be higher: treat this "
                "figure as an approximation."
            ) % {"ltv": pct_str(p.get("ltv_pct"))}
        case _:
            return note.code


def date_str(value: object) -> str:
    """Locale-formatted long date from a date or an ISO string (note params are JSON-safe)."""
    try:
        day = value if isinstance(value, date) else date.fromisoformat(str(value))
        return format_date(day, format="long", locale=_current_locale.get())
    except ValueError:
        return str(value)


def month_str(value: object) -> str:
    """Locale-formatted month and year ("luglio 2026" / "July 2026") from a date or ISO string."""
    try:
        day = value if isinstance(value, date) else date.fromisoformat(str(value))
        return format_date(day, format="LLLL y", locale=_current_locale.get())
    except ValueError:
        return str(value)


def _markup(message: str, **params: object) -> Markup:
    """Fill a translated catalog message that carries markup (``<b>``) with parameters.

    The message is the one trusted part: it comes from our own translation catalog, which is
    where markup in a msgid lives. ``Markup.__mod__`` escapes every parameter, so a value that
    contains ``<`` (or anything a future caller passes in) can never become markup.
    """
    return Markup(message) % params


def tier_short(tier: RenovationTier) -> str:
    """One-word translated name of a renovation tier (the long labels list the scope)."""
    match tier:
        case RenovationTier.NONE:
            return _("no")
        case RenovationTier.LIGHT:
            return _("light")
        case RenovationTier.MEDIUM:
            return _("medium")
        case RenovationTier.FULL:
            return _("full")
        case RenovationTier.PREMIUM:
            return _("premium")
