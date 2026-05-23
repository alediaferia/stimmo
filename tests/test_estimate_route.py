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
