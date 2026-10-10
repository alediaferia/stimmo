"""List the purchase-cost calculator's benchmark ranges that are overdue for review or due soon.

Every non-statutory figure in `src/stimmo/costs/rates.py` carries `as_of` (when it was last checked
against its source) and `review_months` (how long it may be trusted); `as_of` + the interval is its
`due` date. Past `due`, the calculator itself marks the figure "to be checked" for visitors. This
script is the maintainer's side of the same rule: it says what needs a fresh look before then.

Run: `uv run python scripts/check_rates.py [--within DAYS] [--format text|markdown] [--today DATE]`

* `--within N` (default 30): also list figures that fall due in the next N days.
* `--format markdown` prints a table that is usable as a GitHub issue body.
* `--today YYYY-MM-DD` replaces the clock (tests, or "what will be due by then?").

Exit status: 1 when any item is overdue, 0 otherwise. Items that are only due soon are printed but
do not fail the run. No network access: the schedule is read from `rates.py` alone.
The statutory figures (taxes, LTV rules, Consap, renovation bonus) have no interval and are not
listed one by one. One annual reminder, `STATUTORY_AS_OF`, stands for them: it falls due on
15 January of the year after that date, because budget laws take effect on 1 January. It only
notifies the maintainer; the calculator never marks a statutory line "to be checked".
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, timedelta

from stimmo.costs import rates

DEFAULT_WITHIN_DAYS = 30

# What to do when the `STATUTORY_AS_OF` reminder is listed. It is a reminder for the maintainer:
# it marks nothing "to be checked" on the calculator.
_STATUTORY_TEXT = (
    "`STATUTORY_AS_OF` is the yearly reminder to re-verify the statutory figures (taxes, "
    "mortgage LTV rules, Consap guarantee, renovation bonus) against the new budget law, which "
    "takes effect on 1 January. Check each against its cited source in "
    "`src/stimmo/costs/rates.py`, "
    "fix whatever changed, then bump `STATUTORY_AS_OF`. This reminder does not mark any line "
    '"to be checked" on the calculator.'
)


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from None


def _non_negative(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number of days: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def classify(
    today: date, within: int
) -> tuple[list[rates.ReviewItem], list[rates.ReviewItem], list[rates.ReviewItem]]:
    """(overdue, due soon, all) — each sorted by due date, then name.

    Overdue means past `due` (due today is still in time); due soon means due between today and
    `today + within` days inclusive.
    """
    items = sorted(rates.review_schedule(), key=lambda i: (i.due, i.name))
    horizon = today + timedelta(days=within)
    overdue = [i for i in items if i.overdue(today)]
    soon = [i for i in items if not i.overdue(today) and i.due <= horizon]
    return overdue, soon, items


def _source(item: rates.ReviewItem) -> str:
    return item.source_url or "none (no public source)"


def _text(today: date, within: int, overdue, soon, items) -> str:
    lines = [f"Cost calculator rates, checked on {today.isoformat()} (window: {within} days)", ""]
    for title, group in (("OVERDUE", overdue), (f"DUE WITHIN {within} DAYS", soon)):
        if not group:
            continue
        lines.append(f"{title} ({len(group)})")
        for i in group:
            lines += [
                f"  {i.name}",
                f"    as_of {i.as_of.isoformat()}  due {i.due.isoformat()}"
                f"  indicative={'yes' if i.indicative else 'no'}",
                f"    source {_source(i)}",
            ]
        lines.append("")
    if not overdue and not soon:
        nxt = items[0] if items else None
        lines.append(f"Nothing is overdue or due within {within} days.")
        if nxt:
            lines.append(f"Next due: {nxt.name} on {nxt.due.isoformat()}.")
    elif any(i.statutory for i in (*overdue, *soon)):
        lines.append(_STATUTORY_TEXT)
    return "\n".join(lines).rstrip() + "\n"


def _markdown(today: date, within: int, overdue, soon, items) -> str:
    out = ["## Cost calculator: benchmark ranges to review", ""]
    if not overdue and not soon:
        out.append(
            f"As of {today.isoformat()} nothing is overdue or due within {within} days."
            + (f" Next due: `{items[0].name}` on {items[0].due.isoformat()}." if items else "")
        )
        return "\n".join(out) + "\n"
    listed = [*overdue, *soon]
    ranges_overdue = any(not i.statutory for i in overdue)
    out += [
        f"As of {today.isoformat()}: **{len(overdue)} overdue**, {len(soon)} due within "
        f"{within} days."
        # Only a benchmark is ever "to be checked" on the calculator; the statutory reminder is not.
        + (
            ' Overdue ranges are already marked "to be checked" on the calculator.'
            if ranges_overdue
            else ""
        ),
        "",
    ]
    if any(not i.statutory for i in listed):
        out += [
            "**How to refresh a range.** In `src/stimmo/costs/rates.py`, open the constant named "
            "below, re-check the figure against its source, then update its value, `source_url` "
            "and `as_of` (keep `indicative=True` if it cannot be confirmed current). "
            "`RATES_AS_OF` and the page's sitemap `lastmod` follow from `as_of`. Run "
            "`uv run pytest`, then release.",
            "",
        ]
    if any(i.statutory for i in listed):
        out += [f"**Statutory figures.** {_STATUTORY_TEXT}", ""]
    out += [
        "| Status | Constant | Last reviewed | Due | Indicative | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for status, group in (("overdue", overdue), ("due soon", soon)):
        for i in group:
            out.append(
                f"| {status} | `{i.name}` | {i.as_of.isoformat()} | {i.due.isoformat()} "
                f"| {'yes' if i.indicative else 'no'} | {_source(i)} |"
            )
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--within",
        type=_non_negative,
        default=DEFAULT_WITHIN_DAYS,
        metavar="N",
        help=f"also list ranges due within N days (default {DEFAULT_WITHIN_DAYS})",
    )
    parser.add_argument("--format", choices=("text", "markdown"), default="text")
    parser.add_argument(
        "--today", type=_iso_date, default=None, metavar="YYYY-MM-DD", help="override the date"
    )
    args = parser.parse_args(argv)

    today = args.today or date.today()
    overdue, soon, items = classify(today, args.within)
    render = _markdown if args.format == "markdown" else _text
    sys.stdout.write(render(today, args.within, overdue, soon, items))
    return 1 if overdue else 0


if __name__ == "__main__":
    sys.exit(main())
