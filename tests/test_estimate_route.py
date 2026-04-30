from __future__ import annotations

from fastapi.testclient import TestClient

from stimmo.data import amenities, geocode
from stimmo.models import AmenityScore
from stimmo.web.app import app


def test_estimate_renders_result(monkeypatch):
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    client = TestClient(app)
    r = client.post(
        "/estimate",
        data={
            "address": "Piazza Duomo, Milano",
            "surface_m2": "80",
            "property_type": "Abitazioni civili",
            "omi_condition": "NORMALE",
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
        },
    )
    assert r.status_code == 200
    body = r.text
    assert "Estimate" in body
    assert any(v in body for v in ("UNDER-PRICED", "FAIR", "OVER-PRICED"))
