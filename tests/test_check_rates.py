"""`scripts/check_rates.py`: the maintainer's side of the review schedule.

`scripts/` is not a package, so the script is loaded by path. Every test passes `--today` or
freezes the script's clock: the answer depends on the calendar, never on the real date.
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from stimmo.costs import rates
from tests.conftest import COSTS_TODAY

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_rates.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_rates", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_rates = _load_script()

# The Banca d'Italia reference rates: reviewed 2026-10-09, due 2027-01-09.
_THREE_MONTH_NAMES = {
    "MORTGAGE_RATE_AVERAGE",
    "MORTGAGE_RATE_INITIAL_UP_TO_1Y",
    "MORTGAGE_RATE_INITIAL_OVER_1Y",
    "MORTGAGE_TAEG_AVERAGE",
}
_SIX_MONTH_NAMES = {
    "MORTGAGE_INSTRUCTION_FEE",
    "MORTGAGE_APPRAISAL",
    "MORTGAGE_FIRE_INSURANCE",
    "RENOVATION_TIERS",
    "RENOVATION_TECHNICAL_FEES",
    "RENOVATION_CONTINGENCY_PCT",
}
_TWELVE_MONTH_NAMES = {
    "NOTARY_PURCHASE_BANDS[<= 370,000]",
    "NOTARY_PURCHASE_BANDS[<= 465,000]",
    "NOTARY_MORTGAGE_BANDS[<= 370,000]",
    "AGENCY_COMMISSION_PCT",
    "CONFORMITY_CHECK",
    "MOVING_AND_UTILITIES",
}
# The yearly statutory reminder: STATUTORY_AS_OF 2026-10-06 is due 15 January of the next year.
_STATUTORY = "STATUTORY_AS_OF"
_STATUTORY_DUE = "2027-01-15"
_JUST_BEFORE_3M = "2027-01-09"  # the day the 3-month ranges fall due: still in time
_JUST_BEFORE = "2027-04-06"  # the day the 6-month ranges fall due: still in time
_OVERDUE = "2027-04-07"


def _run(capsys, *argv: str) -> tuple[int, str]:
    code = check_rates.main(list(argv))
    return code, capsys.readouterr().out


def _mentioned(out: str, names: set[str]) -> set[str]:
    return {n for n in names if n in out}


class TestExitCode:
    def test_zero_when_everything_is_in_date(self, capsys):
        code, out = _run(capsys, "--today", COSTS_TODAY.isoformat())
        assert code == 0
        assert "Nothing is overdue" in out
        assert "Next due: MORTGAGE_RATE_AVERAGE on 2027-01-09" in out

    def test_one_when_something_is_overdue(self, capsys):
        code, out = _run(capsys, "--today", _OVERDUE)
        assert code == 1
        assert "OVERDUE (11)" in out  # the 10 ranges + the statutory reminder
        assert _mentioned(out.split("DUE WITHIN")[0], _SIX_MONTH_NAMES | _THREE_MONTH_NAMES) == (
            _SIX_MONTH_NAMES | _THREE_MONTH_NAMES
        )

    def test_due_on_the_day_is_not_overdue(self, capsys):
        code, out = _run(capsys, "--today", _JUST_BEFORE_3M, "--within", "0")
        assert code == 0
        assert "OVERDUE" not in out
        assert _mentioned(out, _THREE_MONTH_NAMES) == _THREE_MONTH_NAMES  # listed as due today

    def test_the_day_after_the_due_date_the_reference_rates_are_overdue(self, capsys):
        code, out = _run(capsys, "--today", "2027-01-10")
        assert code == 1
        assert "OVERDUE (4)" in out
        assert _mentioned(out, _THREE_MONTH_NAMES) == _THREE_MONTH_NAMES
        assert _mentioned(out, _SIX_MONTH_NAMES) == set()

    def test_six_month_ranges_are_due_on_the_day_when_the_rest_is_overdue(self, capsys):
        # The 3-month rates are long overdue by then; the 6-month ranges are due today, not overdue.
        code, out = _run(capsys, "--today", _JUST_BEFORE, "--within", "0")
        overdue, _, due_today = out.partition("DUE WITHIN")
        assert code == 1
        assert _mentioned(overdue, _THREE_MONTH_NAMES) == _THREE_MONTH_NAMES
        assert _mentioned(overdue, _SIX_MONTH_NAMES) == set()
        assert _mentioned(due_today, _SIX_MONTH_NAMES) == _SIX_MONTH_NAMES

    def test_due_soon_alone_prints_but_exits_zero(self, capsys):
        code, out = _run(capsys, "--today", "2026-12-20")
        assert code == 0
        assert "OVERDUE" not in out
        assert "DUE WITHIN 30 DAYS (5)" in out  # the 4 rates + the statutory reminder
        assert _mentioned(out, _THREE_MONTH_NAMES) == _THREE_MONTH_NAMES

    def test_everything_overdue_lists_everything(self, capsys):
        code, out = _run(capsys, "--today", "2027-10-07")
        assert code == 1
        assert "OVERDUE (17)" in out
        everything = _THREE_MONTH_NAMES | _SIX_MONTH_NAMES | _TWELVE_MONTH_NAMES | {_STATUTORY}
        assert _mentioned(out, everything) == everything

    def test_overdue_and_due_soon_are_separate_groups(self, capsys):
        # 3- and 6-month ranges overdue; the 12-month ones are 5 months off.
        code, out = _run(capsys, "--today", "2027-09-20", "--within", "30")
        overdue, _, soon = out.partition("DUE WITHIN")
        assert code == 1
        assert _mentioned(overdue, _SIX_MONTH_NAMES | _THREE_MONTH_NAMES) == (
            _SIX_MONTH_NAMES | _THREE_MONTH_NAMES
        )
        assert _mentioned(overdue, _TWELVE_MONTH_NAMES) == set()
        assert _mentioned(soon, _TWELVE_MONTH_NAMES) == _TWELVE_MONTH_NAMES


class TestWithin:
    def test_default_window_is_thirty_days(self, capsys):
        # 2027-04-06 minus 30 days: the first day on which the 6-month ranges are listed.
        _, listed = _run(capsys, "--today", "2027-03-07")
        _, not_yet = _run(capsys, "--today", "2027-03-06")
        assert "DUE WITHIN 30 DAYS" in listed
        assert "DUE WITHIN" not in not_yet

    def test_the_window_is_inclusive(self, capsys):
        _, out = _run(capsys, "--today", "2027-03-25", "--within", "12")  # due in exactly 12 days
        assert _mentioned(out, _SIX_MONTH_NAMES) == _SIX_MONTH_NAMES
        _, out = _run(capsys, "--today", "2027-03-25", "--within", "11")
        assert "DUE WITHIN" not in out  # (the 3-month rates are overdue by then)

    def test_a_wide_window_reaches_the_twelve_month_ranges(self, capsys):
        code, out = _run(capsys, "--today", COSTS_TODAY.isoformat(), "--within", "400")
        assert code == 0  # due soon alone never fails the run
        everything = _THREE_MONTH_NAMES | _SIX_MONTH_NAMES | _TWELVE_MONTH_NAMES
        assert _mentioned(out, everything) == everything

    def test_the_window_is_stated_in_the_heading(self, capsys):
        _, out = _run(capsys, "--today", "2027-03-20", "--within", "45")
        assert "DUE WITHIN 45 DAYS" in out

    @pytest.mark.parametrize("bad", ["-1", "soon", "1.5"])
    def test_a_bad_window_is_a_usage_error(self, bad, capsys):
        with pytest.raises(SystemExit) as exc:
            check_rates.main(["--within", bad])
        assert exc.value.code == 2


class TestOutput:
    def test_text_lists_name_dates_source_and_indicative(self, capsys):
        _, out = _run(capsys, "--today", _OVERDUE)
        assert "MORTGAGE_APPRAISAL" in out
        assert "as_of 2026-10-06  due 2027-04-06  indicative=no" in out
        assert "as_of 2026-10-09  due 2027-01-09  indicative=no" in out  # a reference rate
        assert rates.MORTGAGE_RATE_AVERAGE.source_url in out
        assert "indicative=yes" in out  # the renovation ranges
        assert rates.MORTGAGE_APPRAISAL.source_url in out
        assert rates.RENOVATION_SOURCE_URL in out

    def test_a_figure_without_a_source_says_so(self, capsys):
        _, out = _run(capsys, "--today", "2027-10-07")
        assert "none (no public source)" in out

    def test_markdown_is_a_table_usable_as_an_issue_body(self, capsys):
        code, out = _run(capsys, "--today", _OVERDUE, "--format", "markdown")
        assert code == 1
        lines = out.splitlines()
        assert lines[0].startswith("## ")
        header = "| Status | Constant | Last reviewed | Due | Indicative | Source |"
        assert header in lines
        rows = [ln for ln in lines if ln.startswith("| overdue |")]
        assert len(rows) == 11
        for ln in rows:
            assert ln.count("|") == header.count("|")  # no cell breaks the table
        mortgage = next(ln for ln in rows if "`MORTGAGE_APPRAISAL`" in ln)
        assert "2026-10-06" in mortgage and "2027-04-06" in mortgage
        assert rates.MORTGAGE_APPRAISAL.source_url in mortgage
        assert "| no |" in mortgage

    def test_markdown_header_says_how_to_refresh(self, capsys):
        _, out = _run(capsys, "--today", _OVERDUE, "--format", "markdown")
        head = out.split("| Status")[0]
        assert "src/stimmo/costs/rates.py" in head
        for word in ("value", "source_url", "as_of"):
            assert word in head
        assert "11 overdue" in head

    def test_markdown_lists_due_soon_rows_with_their_own_status(self, capsys):
        code, out = _run(capsys, "--today", "2026-12-20", "--format", "markdown")
        assert code == 0
        assert "| due soon |" in out and "| overdue |" not in out
        assert "**0 overdue**, 5 due within 30 days" in out

    def test_markdown_when_all_in_date_has_no_table(self, capsys):
        code, out = _run(capsys, "--today", COSTS_TODAY.isoformat(), "--format", "markdown")
        assert code == 0
        assert "| Status" not in out
        assert "nothing is overdue" in out

    def test_format_defaults_to_text(self, capsys):
        _, out = _run(capsys, "--today", _OVERDUE)
        assert "| Status" not in out and not out.startswith("## ")

    def test_unknown_format_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            check_rates.main(["--format", "html"])
        assert exc.value.code == 2

    @pytest.mark.parametrize("bad", ["2027-13-01", "07/04/2027", "tomorrow"])
    def test_a_bad_date_is_a_usage_error(self, bad):
        with pytest.raises(SystemExit) as exc:
            check_rates.main(["--today", bad])
        assert exc.value.code == 2


class TestInterface:
    """The workflow calls exactly `check_rates.py --format markdown --within 30`."""

    class _FrozenDate(date):
        _today = COSTS_TODAY

        @classmethod
        def today(cls):
            return cls._today

    def test_the_workflow_invocation_with_no_today_uses_the_clock(self, monkeypatch, capsys):
        monkeypatch.setattr(check_rates, "date", self._FrozenDate)
        assert _run(capsys, "--format", "markdown", "--within", "30")[0] == 0
        monkeypatch.setattr(self._FrozenDate, "_today", date(2027, 4, 7))
        code, out = _run(capsys, "--format", "markdown", "--within", "30")
        assert code == 1 and "| overdue |" in out

    def test_it_needs_no_network(self, monkeypatch, capsys):
        def no_network(*_a, **_k):
            raise AssertionError("check_rates must not touch the network")

        monkeypatch.setattr(socket.socket, "connect", no_network)
        monkeypatch.setattr(socket, "getaddrinfo", no_network)
        assert _run(capsys, "--today", _OVERDUE, "--format", "markdown")[0] == 1

    def test_it_runs_as_a_standalone_script(self):
        done = subprocess.run(
            [sys.executable, str(_SCRIPT), "--today", _OVERDUE, "--format", "markdown"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 1, done.stderr
        assert "| overdue |" in done.stdout
        ok = subprocess.run(
            [sys.executable, str(_SCRIPT), "--today", COSTS_TODAY.isoformat()],
            capture_output=True,
            text=True,
            check=False,
        )
        assert ok.returncode == 0, ok.stderr

    def test_statutory_figures_are_never_listed_one_by_one(self, capsys):
        # One annual reminder (STATUTORY_AS_OF) stands for all of them; no tax, rule or bonus
        # constant is a row of its own.
        _, out = _run(capsys, "--today", "2040-01-01")
        for statutory in ("REGISTRO", "IMPOSTA", "BONUS", "CONSAP", "MUTUO"):
            assert statutory not in out
        assert _STATUTORY in out


class TestStatutoryReminder:
    """`STATUTORY_AS_OF` is due on 15 January of the year after it, and only ever notifies."""

    def _listed(self, out: str) -> bool:
        return _STATUTORY in out

    def test_it_is_not_listed_while_it_is_more_than_the_window_away(self, capsys):
        code, out = _run(capsys, "--today", "2026-12-15")  # due in 31 days
        assert not self._listed(out)
        assert code == 0

    def test_it_is_listed_as_due_soon_when_the_window_reaches_it(self, capsys):
        code, out = _run(capsys, "--today", "2026-12-16")  # due in exactly 30 days
        soon = out.partition("DUE WITHIN")[2]
        assert self._listed(soon) and "OVERDUE" not in out
        assert f"as_of 2026-10-06  due {_STATUTORY_DUE}  indicative=no" in out
        assert code == 0  # due soon alone never fails the run

    def test_it_is_listed_on_the_due_date_and_is_not_overdue_yet(self, capsys):
        # (The reference rates fell due on 2027-01-09, so by the 15th they are overdue.)
        _, out = _run(capsys, "--today", _STATUTORY_DUE, "--within", "0")
        overdue, _, today = out.partition("DUE WITHIN")
        assert self._listed(today) and not self._listed(overdue)

    def test_the_day_after_the_due_date_it_is_overdue_and_the_run_fails(self, capsys, monkeypatch):
        # Isolate the reminder: nothing else may be overdue, so the exit status is its own.
        monkeypatch.setattr(rates, "review_schedule", lambda: (rates.statutory_review(),))
        code, out = _run(capsys, "--today", _STATUTORY_DUE)
        assert code == 0 and "OVERDUE" not in out
        code, out = _run(capsys, "--today", "2027-01-16")
        assert code == 1
        assert "OVERDUE (1)" in out and self._listed(out)

    def test_after_the_due_date_it_stays_overdue_until_the_figures_are_re_verified(self, capsys):
        _, out = _run(capsys, "--today", "2027-02-01")
        assert self._listed(out.partition("DUE WITHIN")[0])

    def test_bumping_statutory_as_of_moves_the_reminder_to_the_next_january(
        self, capsys, monkeypatch
    ):
        monkeypatch.setattr(rates, "STATUTORY_AS_OF", date(2027, 1, 20))
        _, out = _run(capsys, "--today", "2027-02-01", "--within", "400")
        assert "as_of 2027-01-20  due 2028-01-15" in out
        assert _STATUTORY in out.partition("DUE WITHIN")[2]  # due soon again, no longer overdue

    def test_the_markdown_row_and_instructions(self, capsys):
        code, out = _run(capsys, "--today", "2027-01-16", "--format", "markdown")
        assert code == 1
        row = next(ln for ln in out.splitlines() if ln.startswith(f"| overdue | `{_STATUTORY}`"))
        assert "2026-10-06" in row and _STATUTORY_DUE in row
        assert rates.AGENZIA_ENTRATE_GUIDE_URL in row
        assert row.count("|") == 7  # six cells, as in the header
        head = out.split("| Status")[0]
        assert "budget law" in head
        assert "rates.py" in head
        assert "bump `STATUTORY_AS_OF`" in head
        assert "stimmo-growth" not in out  # the private repo is not named

    def test_the_markdown_instructions_appear_only_when_the_reminder_is_listed(self, capsys):
        # Only the reference rates are due soon: the reminder is 36 days off.
        _, out = _run(capsys, "--today", "2026-12-10", "--format", "markdown")
        assert "budget law" not in out
        assert "How to refresh a range" in out

    def test_a_reminder_alone_does_not_claim_the_calculator_marks_anything(
        self, capsys, monkeypatch
    ):
        monkeypatch.setattr(rates, "review_schedule", lambda: (rates.statutory_review(),))
        code, out = _run(capsys, "--today", "2027-01-16", "--format", "markdown")
        assert code == 1
        assert "**1 overdue**" in out
        assert "Overdue ranges are already marked" not in out
        assert "How to refresh a range" not in out
        # The workflow keys on this heading and the table header.
        assert out.splitlines()[0] == "## Cost calculator: benchmark ranges to review"
        assert "| Status | Constant |" in out
