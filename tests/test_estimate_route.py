from __future__ import annotations

from fastapi.testclient import TestClient

from stimmo.data import amenities, geocode
from stimmo.models import AmenityScore
from stimmo.web.app import app


def _base_form_data() -> dict[str, str]:
    return {
        "address": "Piazza Duomo, Milano",
        "surface_m2": "80",
        "property_type": "Abitazioni civili",
        "fine_condition": "abitabile",
        "floor": "2",
        "total_floors": "5",
        "has_lift": "on",
        "energy_class": "",
        "outdoor": "none",
        "has_box": "off",
        "construction_era": "postwar_boom",
        "orientation": "mixed",
        "has_second_bathroom": "off",
        "asking_price_eur": "650000",
    }


def test_estimate_renders_result(monkeypatch):
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    client = TestClient(app)
    r = client.post("/en/estimate", data=_base_form_data())
    assert r.status_code == 200
    body = r.text
    assert "Report" in body
    verdicts = ("Verdict · Under-priced", "Verdict · Fair", "Verdict · Over-priced")
    assert any(v in body for v in verdicts)


def test_estimate_offers_alternatives_when_derived_condition_missing(monkeypatch):
    """When the derived OMI bucket has no quote for the zone but other buckets
    do, the user is shown an alternatives picker rather than a silent fallback."""
    # D10 has NORMALE but not SCADENTE for civili — use fine_condition=da
    # ristrutturare to derive SCADENTE.
    from stimmo.data import zones as zones_mod

    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.0, 9.0))
    monkeypatch.setattr(zones_mod, "zone_for_point", lambda lat, lon: ("D10", "Test zone"))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    client = TestClient(app)
    data = _base_form_data()
    data["fine_condition"] = "da ristrutturare"
    r = client.post("/en/estimate", data=data)
    assert r.status_code == 200
    body = r.text
    assert "No OMI data" in body or "OMI condition mismatch" in body
    # Picker should offer at least one available bucket as a retry button.
    assert "omi_condition_override" in body
    assert "NORMALE" in body


def test_estimate_completes_when_override_supplied(monkeypatch):
    """Submitting with omi_condition_override bypasses the alternatives check
    and runs the estimate against the chosen bucket."""
    from stimmo.data import zones as zones_mod

    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.0, 9.0))
    monkeypatch.setattr(zones_mod, "zone_for_point", lambda lat, lon: ("D10", "Test zone"))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    client = TestClient(app)
    data = _base_form_data()
    data["fine_condition"] = "da ristrutturare"
    data["omi_condition_override"] = "NORMALE"
    r = client.post("/en/estimate", data=data)
    assert r.status_code == 200
    assert "Report" in r.text


# ---------------------------------------------------------------------------
# Amenity budget, deferred (progressive) state, client coordinates
# ---------------------------------------------------------------------------

import re  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

from stimmo.web import app as app_module  # noqa: E402
from stimmo.web import metrics  # noqa: E402

_DUOMO = (45.4642, 9.1900)
_OUTSIDE_MILANO = (45.0, 9.0)


def _counter(name: str, **labels) -> float:
    for m in metrics.prometheus_client.REGISTRY.collect():
        for s in m.samples:
            if s.name == name and s.labels == labels:
                return s.value
    return 0.0


@pytest.fixture()
def slow_amenities(monkeypatch):
    """fetch_amenities that blocks until released; budget shrunk so the test is quick."""
    gate = threading.Event()
    monkeypatch.setattr(app_module, "AMENITY_BUDGET_S", 0.2)
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": _DUOMO)

    def slow(lat, lon):
        gate.wait(10)
        return AmenityScore(metro_within_500m=1, score_pct=2.0)

    monkeypatch.setattr(amenities, "fetch_amenities", slow)
    yield gate
    gate.set()


def test_slow_overpass_renders_deferred_page_quickly(slow_amenities):
    before = _counter("stimmo_estimate_amenity_total", status="deferred")
    t0 = time.monotonic()
    r = TestClient(app).post("/en/estimate", data=_base_form_data())
    assert time.monotonic() - t0 < 2.0
    assert r.status_code == 200
    body = r.text
    assert any(
        v in body for v in ("Verdict · Under-priced", "Verdict · Fair", "Verdict · Over-priced")
    )
    # Accessible loading state + the visible note that the verdict excludes amenities.
    assert 'id="amenity-loading"' in body
    assert 'aria-busy="true"' in body and 'aria-live="polite"' in body
    assert "Loading nearby services" in body
    assert "does not include the amenity adjustment" in body
    # No-JS path: a plain form that recomputes, carrying the resolved coordinates.
    assert 'id="recompute-form"' in body and 'name="lat"' in body
    # Share link must reflect what the visitor finally sees: none while deferred.
    assert 'id="share-btn"' not in body and "/og/" not in body
    assert _counter("stimmo_estimate_amenity_total", status="deferred") == before + 1


def test_status_endpoint_polls_then_recompute_has_amenities(slow_amenities):
    client = TestClient(app)
    r = client.post("/en/estimate", data=_base_form_data())
    assert 'id="amenity-loading"' in r.text
    url = "/en/api/amenities/status"
    q = {"lat": _DUOMO[0], "lon": _DUOMO[1]}
    assert client.get(url, params=q).json() == {"status": "pending"}
    slow_amenities.set()
    for _ in range(100):
        if client.get(url, params=q).json()["status"] == "ok":
            break
        time.sleep(0.02)
    assert client.get(url, params=q).json() == {"status": "ok"}

    # The replay the page's script performs: its own form fields, lat/lon included.
    fields = dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', r.text))
    assert fields["lat"] and fields["lon"]
    r2 = client.post("/en/estimate", data=fields)
    assert r2.status_code == 200
    assert 'id="amenity-loading"' not in r2.text
    assert 'id="share-btn"' in r2.text  # the final page mints the share link
    assert "Combined amenity score" in r2.text and "+2.00" in r2.text


def test_status_endpoint_reports_failure_and_rejects_non_milano(monkeypatch):
    def boom(lat, lon):
        raise amenities.AmenityFetchError("down", attempts=2)

    monkeypatch.setattr(amenities, "fetch_amenities", boom)
    client = TestClient(app)
    q = {"lat": _DUOMO[0], "lon": _DUOMO[1]}
    for _ in range(100):
        body = client.get("/en/api/amenities/status", params=q).json()
        if body["status"] != "pending":
            break
        time.sleep(0.02)
    assert body["status"] == "failed" and "down" in body["error"]
    assert (
        client.get(
            "/en/api/amenities/status",
            params={"lat": _OUTSIDE_MILANO[0], "lon": _OUTSIDE_MILANO[1]},
        ).status_code
        == 400
    )


def test_failed_amenities_still_render_the_failed_state(monkeypatch):
    def boom(lat, lon):
        raise amenities.AmenityFetchError("down", attempts=2)

    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": _DUOMO)
    monkeypatch.setattr(amenities, "fetch_amenities", boom)
    before = _counter("stimmo_estimate_amenity_total", status="failed")
    r = TestClient(app).post("/en/estimate", data=_base_form_data())
    assert r.status_code == 200
    assert "Amenity data unavailable" in r.text and 'id="amenity-loading"' not in r.text
    assert _counter("stimmo_estimate_amenity_total", status="failed") == before + 1


def test_cached_amenities_are_counted_and_not_deferred(monkeypatch):
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": _DUOMO)
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())
    client = TestClient(app)
    client.post("/en/estimate", data=_base_form_data())
    before = _counter("stimmo_estimate_amenity_total", status="cached")
    r = client.post("/en/estimate", data=_base_form_data())
    assert 'id="amenity-loading"' not in r.text
    assert _counter("stimmo_estimate_amenity_total", status="cached") == before + 1


def _fail_geocode(addr, *, city="Milano"):
    raise AssertionError("geocode must not be called when valid client coordinates are sent")


def test_valid_client_coordinates_skip_geocoding(monkeypatch):
    monkeypatch.setattr(geocode, "geocode", _fail_geocode)
    seen = []
    monkeypatch.setattr(
        amenities, "fetch_amenities", lambda lat, lon: seen.append((lat, lon)) or AmenityScore()
    )
    data = {**_base_form_data(), "lat": str(_DUOMO[0]), "lon": str(_DUOMO[1])}
    r = TestClient(app).post("/en/estimate", data=data)
    assert r.status_code == 200
    assert seen == [_DUOMO]


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (str(_OUTSIDE_MILANO[0]), str(_OUTSIDE_MILANO[1])),  # parses, but outside Milano
        ("abc", "9.19"),  # malformed
        ("nan", "nan"),
        ("45.46", ""),
        ("", ""),
    ],
)
def test_bad_client_coordinates_fall_back_to_geocoding(monkeypatch, lat, lon):
    calls = []

    def fake_geocode(addr, *, city="Milano"):
        calls.append(addr)
        return _DUOMO

    monkeypatch.setattr(geocode, "geocode", fake_geocode)
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())
    r = TestClient(app).post("/en/estimate", data={**_base_form_data(), "lat": lat, "lon": lon})
    assert r.status_code == 200
    assert calls == ["Piazza Duomo, Milano"]


def test_form_has_hidden_coordinate_fields():
    r = TestClient(app).get("/en/")
    assert 'name="lat"' in r.text and 'name="lon"' in r.text
    assert 'id="geo_lat"' in r.text


def test_prefilled_form_has_empty_coordinates_and_estimate_geocodes(monkeypatch):
    """The import/bookmarklet prefill fills fields but never coordinates (they stay empty
    until the visitor picks a suggestion), so the estimate geocodes exactly as before."""
    r = TestClient(app).get("/en/", params={"address": "Via Torino 1, Milano"})
    assert r.status_code == 200
    assert re.search(r'id="geo_lat" name="lat" value=""', r.text)
    calls = []
    monkeypatch.setattr(
        geocode, "geocode", lambda addr, *, city="Milano": calls.append(addr) or _DUOMO
    )
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())
    data = {**_base_form_data(), "lat": "", "lon": ""}
    assert TestClient(app).post("/en/estimate", data=data).status_code == 200
    assert calls


def test_status_route_label_is_the_endpoint_name(monkeypatch):
    """Dispatcher metrics label the poll by function name, not by /{lang}/... path."""
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())
    client = TestClient(app_module.application)
    for lang in ("it", "en"):
        client.get(f"/{lang}/api/amenities/status", params={"lat": _DUOMO[0], "lon": _DUOMO[1]})
    routes = {
        s.labels["route"]
        for m in metrics.prometheus_client.REGISTRY.collect()
        if m.name == "stimmo_http_requests"
        for s in m.samples
    }
    assert "api_amenities_status" in routes
    assert not any("/" in r for r in routes)
