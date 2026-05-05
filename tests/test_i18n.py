from __future__ import annotations

import pytest

from stimmo.i18n import (
    fmt_eur,
    fmt_pct,
    fmt_semester,
    gettext,
    gettext_lazy,
    negotiate_locale,
    use_locale,
)


def test_gettext_returns_string_for_known_locale():
    # Verifies locale switching doesn't raise; translation content depends on
    # whether translate_po.py has been run (msgstr may be empty → falls back to msgid).
    with use_locale("it_IT"):
        result_it = gettext("Estimate")
    with use_locale("en_US"):
        result_en = gettext("Estimate")
    assert isinstance(result_it, str)
    assert isinstance(result_en, str)
    # en_US always returns msgid as-is (empty msgstr in catalog)
    assert result_en == "Estimate"


def test_lazy_str_evaluates_at_str_time():
    # _LazyStr must not call gettext at construction time — only at str() time.
    lazy = gettext_lazy("Verdict")
    with use_locale("en_US"):
        assert str(lazy) == "Verdict"
    # Switching locale between calls shows deferred evaluation.
    with use_locale("it_IT"):
        result = str(lazy)
    assert isinstance(result, str)


def test_fmt_eur_italian_uses_period_thousands():
    with use_locale("it_IT"):
        result = fmt_eur(1_234_567)
    assert "." in result
    assert "€" in result


def test_fmt_eur_english_uses_comma_thousands():
    with use_locale("en_US"):
        result = fmt_eur(1_234_567)
    assert "," in result
    assert "€" in result


def test_fmt_semester_italian():
    with use_locale("it_IT"):
        assert fmt_semester("2024-2") == "2° sem. 2024"


def test_fmt_semester_english():
    with use_locale("en_US"):
        assert fmt_semester("2024-2") == "H2 2024"


def test_negotiate_locale_cookie_it():
    assert negotiate_locale("it", None) == "it_IT"


def test_negotiate_locale_cookie_en():
    assert negotiate_locale("en", None) == "en_US"


def test_negotiate_locale_accept_language_english():
    assert negotiate_locale(None, "en-US,en;q=0.9") == "en_US"


def test_negotiate_locale_accept_language_italian():
    assert negotiate_locale(None, "it-IT,it;q=0.9,en;q=0.8") == "it_IT"


def test_negotiate_locale_defaults_to_italian():
    assert negotiate_locale(None, None) == "it_IT"


def test_negotiate_locale_unknown_cookie_falls_through():
    assert negotiate_locale("fr", "en-US") == "en_US"
