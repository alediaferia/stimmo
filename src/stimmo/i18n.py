"""Internationalization infrastructure for stimmo.

Source language: English (en_US). Translations live in src/stimmo/locale/.
Locale codes used internally: 'it_IT', 'en_US'. URL lang slugs: 'it', 'en'.
"""
from __future__ import annotations

import gettext as _gettext
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Generator

from babel.numbers import format_decimal

SUPPORTED_LANGS: frozenset[str] = frozenset({"it", "en"})
LANG_TO_LOCALE: dict[str, str] = {"it": "it_IT", "en": "en_US"}
LOCALE_TO_LANG: dict[str, str] = {"it_IT": "it", "en_US": "en"}

_LOCALE_DIR = Path(__file__).parent / "locale"
_DOMAIN = "messages"

# Per-request locale — defaults to Italian (home audience).
_current_locale: ContextVar[str] = ContextVar("stimmo_locale", default="it_IT")

_catalogs: dict[str, _gettext.NullTranslations] = {}


def _load_catalog(locale: str) -> _gettext.NullTranslations:
    if locale not in _catalogs:
        try:
            t = _gettext.translation(_DOMAIN, localedir=str(_LOCALE_DIR), languages=[locale])
        except FileNotFoundError:
            t = _gettext.NullTranslations()
        _catalogs[locale] = t
    return _catalogs[locale]


def gettext(message: str) -> str:
    return _load_catalog(_current_locale.get()).gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _load_catalog(_current_locale.get()).ngettext(singular, plural, n)


@contextmanager
def use_locale(locale: str) -> Generator[None, None, None]:
    """Temporarily set the active locale (for tests or batch rendering)."""
    token = _current_locale.set(locale)
    try:
        yield
    finally:
        _current_locale.reset(token)


class _LazyStr:
    """Deferred-translation string. Evaluates via current locale at str() time."""

    __slots__ = ("_key",)

    def __init__(self, key: str) -> None:
        self._key = key

    def __str__(self) -> str:
        return gettext(self._key)

    def __repr__(self) -> str:
        return f"lazy_gettext({self._key!r})"

    def __format__(self, spec: str) -> str:
        return format(str(self), spec)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _LazyStr):
            return self._key == other._key
        if isinstance(other, str):
            return str(self) == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._key)

    def __add__(self, other: str) -> str:
        return str(self) + other

    def __radd__(self, other: str) -> str:
        return other + str(self)


def gettext_lazy(key: str) -> _LazyStr:
    """Return a lazy translation string — translated when str() is called."""
    return _LazyStr(key)


# ---------------------------------------------------------------------------
# Number / currency formatters
# ---------------------------------------------------------------------------

def fmt_eur(n: float, locale: str | None = None) -> str:
    """Format as Euro currency according to locale conventions."""
    loc = locale or _current_locale.get()
    formatted = format_decimal(round(n), format="#,##0", locale=loc)
    return ("€ " if loc.startswith("it") else "€") + formatted


def fmt_pct(n: float, locale: str | None = None, digits: int = 1) -> str:
    """Format a percentage with sign prefix."""
    loc = locale or _current_locale.get()
    sign = "+" if n >= 0 else ""
    fmt = "0." + "0" * digits
    return sign + format_decimal(n, format=fmt, locale=loc) + "%"


def fmt_semester(semester: str, locale: str | None = None) -> str:
    """Format '2024-2' → 'H2 2024' (en) or '2° sem. 2024' (it)."""
    loc = locale or _current_locale.get()
    try:
        year, half = semester.split("-")
        if loc.startswith("it"):
            return f"{half}° sem. {year}"
        return f"H{half} {year}"
    except (ValueError, AttributeError):
        return semester


def negotiate_locale(cookie: str | None, accept_language: str | None) -> str:
    """Resolve locale: stimmo_lang cookie → Accept-Language → default 'it_IT'."""
    if cookie in LANG_TO_LOCALE:
        return LANG_TO_LOCALE[cookie]
    if accept_language:
        for part in accept_language.split(","):
            lang = part.split(";")[0].strip().lower()
            if lang.startswith("it"):
                return "it_IT"
            if lang.startswith("en"):
                return "en_US"
    return "it_IT"
