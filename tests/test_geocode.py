"""Tests for `data/geocode.py`, in particular the `Via Privata` fallback.

Milano's formerly-private roads carry a Privata/Privato qualifier in their
official name that Nominatim does not always index; `geocode()` retries once
with the qualifier dropped. See the module docstring in `data/geocode.py`.
"""

from __future__ import annotations

import pytest

from stimmo.data import geocode

# Real repro: immobiliare.it listing 130921538, which dead-ended at a 400
# because Nominatim has no "Via Privata Martiri Triestini".
MARTIRI_TRIESTINI = "Via Privata Martiri Triestini, 5, Milano"
MARTIRI_TRIESTINI_COORDS = (45.475, 9.1425)


class _FakeResponse:
    def __init__(self, payload: list[dict]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return self._payload


@pytest.fixture()
def nominatim(monkeypatch: pytest.MonkeyPatch):
    """Stubs out the HTTP call and the 1.1s throttle.

    Returns a recorder holding the `q` of every request issued and the number
    of times `_throttle()` ran, and taking a `responses` callable that maps a
    query string to the Nominatim payload for it.
    """

    class _Recorder:
        def __init__(self) -> None:
            self.queries: list[str] = []
            self.throttles = 0
            self.responses = lambda q: []

    rec = _Recorder()

    def fake_get(url, *, params, headers, timeout):
        rec.queries.append(params["q"])
        return _FakeResponse(rec.responses(params["q"]))

    def fake_throttle() -> None:
        rec.throttles += 1

    monkeypatch.setattr(geocode.requests, "get", fake_get)
    monkeypatch.setattr(geocode, "_throttle", fake_throttle)
    return rec


def test_via_privata_falls_back_to_bare_street_name(nominatim):
    """The listing address resolves via the stripped-qualifier retry."""
    lat, lon = MARTIRI_TRIESTINI_COORDS

    def responses(q: str) -> list[dict]:
        if "Privata" in q:
            return []
        return [{"lat": str(lat), "lon": str(lon)}]

    nominatim.responses = responses

    assert geocode.geocode(MARTIRI_TRIESTINI) == (lat, lon)
    # Full name first, stripped only as a fallback.
    assert len(nominatim.queries) == 2
    assert nominatim.queries[0].startswith("Via Privata Martiri Triestini, 5, Milano")
    assert nominatim.queries[1].startswith("Via Martiri Triestini, 5, Milano")


def test_address_as_given_wins(nominatim):
    """A street genuinely indexed under its full name costs one request."""
    nominatim.responses = lambda q: [{"lat": "45.5", "lon": "9.2"}]

    assert geocode.geocode(MARTIRI_TRIESTINI) == (45.5, 9.2)
    assert len(nominatim.queries) == 1
    assert "Privata" in nominatim.queries[0]


def test_plain_address_issues_no_extra_request(nominatim):
    """No qualifier to strip -> exactly one lookup, hit or miss."""
    nominatim.responses = lambda q: []

    with pytest.raises(LookupError):
        geocode.geocode("Via Dante, 1, Milano")
    assert len(nominatim.queries) == 1


def test_every_request_is_throttled(nominatim):
    """The fallback goes through `_throttle()` too — no unthrottled extra call."""
    nominatim.responses = lambda q: []

    with pytest.raises(LookupError):
        geocode.geocode(MARTIRI_TRIESTINI)
    assert nominatim.throttles == len(nominatim.queries) == 2


def test_lookup_error_names_the_original_address(nominatim):
    """The 400 the user sees quotes what they typed, not the retry variant."""
    nominatim.responses = lambda q: []

    with pytest.raises(LookupError, match=r"Via Privata Martiri Triestini, 5, Milano"):
        geocode.geocode(MARTIRI_TRIESTINI)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        (
            "Via Privata Martiri Triestini, 5",
            ["Via Privata Martiri Triestini, 5", "Via Martiri Triestini, 5"],
        ),
        ("viale privato Monza, 2", ["viale privato Monza, 2", "viale Monza, 2"]),
        ("Piazza Privata Grandi, 1", ["Piazza Privata Grandi, 1", "Piazza Grandi, 1"]),
        # Not anchored at the start, or not a qualifier: left alone.
        ("Via Dante, 1", ["Via Dante, 1"]),
        ("Via della Privata Speranza, 3", ["Via della Privata Speranza, 3"]),
        ("Via Privatista, 4", ["Via Privatista, 4"]),
    ],
)
def test_variants(address: str, expected: list[str]):
    assert geocode._variants(address) == expected
