from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from stimmo.data.importers.immobiliare import parse
from stimmo.models import (
    ConstructionEra,
    EnergyClass,
    FineCondition,
    OmiCondition,
    Outdoor,
    PropertyType,
)
from stimmo.web.app import app

FIXTURE_DIR = Path(__file__).parent / "data"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


class TestParserHappyPath:
    def test_real_sample(self):
        payload = _load("immobiliare.it.sample.json")
        result = parse(payload)

        assert result["address"] == "Via Sintetica, 10, Milano"
        assert result["surface_m2"] == 120
        assert result["asking_price_eur"] == 400000
        assert result["property_type"] == PropertyType.SIGNORILI.value
        assert result["floor"] == 4
        assert result["total_floors"] == 4
        assert result["has_lift"] is True
        assert result["energy_class"] == EnergyClass.E.value
        assert result["omi_condition"] == OmiCondition.NORMALE.value
        assert result["fine_condition"] == FineCondition.ABITABILE.value
        assert result["has_second_bathroom"] is True
        assert result["outdoor"] == Outdoor.BALCONY.value
        assert result["has_box"] is False
        assert result["construction_era"] == ConstructionEra.POSTWAR_BOOM.value


class TestParserEdgeCases:
    def test_villa(self):
        payload = _load("immobiliare_villa.json")
        result = parse(payload)

        assert result["property_type"] == PropertyType.VILLE.value
        assert result["has_lift"] is False
        assert result["floor"] == 0
        assert result["energy_class"] == EnergyClass.D.value

    def test_floor_codes(self):
        payload = _load("immobiliare_floor_codes.json")
        result = parse(payload)

        # "T" (terraza) maps to floor 0
        assert result["floor"] == 0
        assert result["total_floors"] == 5

    def test_sparse_fields(self):
        payload = _load("immobiliare_sparse.json")
        result = parse(payload)

        # Only mandatory fields present
        assert result["address"] == "Via Dante, 20, Milano"
        assert result["surface_m2"] == 80
        assert result["asking_price_eur"] == 400000

        # Missing fields are omitted
        assert "floor" not in result
        assert "energy_class" not in result
        assert "construction_era" not in result
        assert "property_type" not in result


class TestParserFieldExtraction:
    def test_address_assembly(self):
        payload = {"location": {"address": "Via Test", "streetNumber": "10", "city": "Milano"}}
        result = parse(payload)
        assert result["address"] == "Via Test, 10, Milano"

    def test_address_missing_parts(self):
        payload = {"location": {"address": "Via Test", "city": "Milano"}}
        result = parse(payload)
        assert result["address"] == "Via Test, Milano"

    def test_surface_with_comma_decimal(self):
        payload = {"surface": "153,0 m²"}
        result = parse(payload)
        assert result["surface_m2"] == 153

    def test_energy_class_A1_to_A(self):
        payload = {"energy": {"class": {"name": "A1"}}}
        result = parse(payload)
        assert result["energy_class"] == EnergyClass.A.value

    def test_bathrooms_second_bath(self):
        payload = {"bathrooms": "2"}
        result = parse(payload)
        assert result["has_second_bathroom"] is True

    def test_bathrooms_single(self):
        payload = {"bathrooms": "1"}
        result = parse(payload)
        assert result["has_second_bathroom"] is False

    def test_floor_S_basement(self):
        payload = {"floor": {"abbreviation": "S"}, "floors": "4 piani"}
        result = parse(payload)
        assert result["floor"] == -1

    def test_floor_R_raised(self):
        payload = {"floor": {"abbreviation": "R"}, "floors": "4 piani"}
        result = parse(payload)
        assert result["floor"] == 0

    def test_floor_A_attic(self):
        payload = {"floor": {"abbreviation": "A"}, "floors": "5 piani"}
        result = parse(payload)
        assert result["floor"] == 5  # attic = total_floors

    def test_floor_numeric(self):
        payload = {"floor": {"abbreviation": "3"}, "floors": "4 piani"}
        result = parse(payload)
        assert result["floor"] == 3

    def test_condition_by_id_confirmed(self):
        payload = {"conditionId": 2}
        result = parse(payload)
        assert result["omi_condition"] == OmiCondition.NORMALE.value
        assert result["fine_condition"] == FineCondition.ABITABILE.value

    def test_condition_by_string_fallback(self):
        payload = {"condition": "Ottimo / Ristrutturato"}
        result = parse(payload)
        assert result["omi_condition"] == OmiCondition.OTTIMO.value
        assert result["fine_condition"] == FineCondition.RISTRUTTURATO.value

    def test_outdoor_balcony(self):
        payload = {
            "primaryFeatures": [
                {"codeName": "balcony", "isVisible": True},
            ]
        }
        result = parse(payload)
        assert result["outdoor"] == Outdoor.BALCONY.value

    def test_outdoor_terrace(self):
        payload = {
            "primaryFeatures": [
                {"codeName": "terrace", "isVisible": True},
            ]
        }
        result = parse(payload)
        assert result["outdoor"] == Outdoor.TERRACE_SMALL.value

    def test_has_box_parking(self):
        payload = {
            "primaryFeatures": [
                {"codeName": "garage", "value": 1},
            ]
        }
        result = parse(payload)
        assert result["has_box"] is True

    def test_has_box_excludes_basement(self):
        payload = {
            "primaryFeatures": [
                {"codeName": "basement", "isVisible": True},
                {"codeName": "cantina", "isVisible": True},
            ]
        }
        result = parse(payload)
        assert result["has_box"] is False

    def test_construction_era_prewar(self):
        payload = {"buildingYear": 1920}
        result = parse(payload)
        assert result["construction_era"] == ConstructionEra.PRE_WAR.value

    def test_construction_era_postwar_boom(self):
        payload = {"buildingYear": 1970}
        result = parse(payload)
        assert result["construction_era"] == ConstructionEra.POSTWAR_BOOM.value

    def test_construction_era_eighties_90s(self):
        payload = {"buildingYear": 1995}
        result = parse(payload)
        assert result["construction_era"] == ConstructionEra.EIGHTIES_90S.value

    def test_construction_era_contemporary(self):
        payload = {"buildingYear": 2010}
        result = parse(payload)
        assert result["construction_era"] == ConstructionEra.CONTEMPORARY.value

    def test_construction_era_recent(self):
        payload = {"buildingYear": 2020}
        result = parse(payload)
        assert result["construction_era"] == ConstructionEra.RECENT.value

    def test_orientation_south(self):
        payload = {"description": "Esposizione sud, molto luminoso"}
        result = parse(payload)
        assert result["orientation"] == "south"

    def test_orientation_north(self):
        payload = {"description": "Esposizione nord, fresco"}
        result = parse(payload)
        assert result["orientation"] == "north"


class TestRoutes:
    def test_import_get_valid_payload(self):
        payload = _load("immobiliare.it.sample.json")
        b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        client = TestClient(app)
        r = client.get(f"/import?src=immobiliare&v=1&p={b64}")

        assert r.status_code == 200
        assert "Prefilled from" in r.text or "form" in r.text.lower()

    def test_import_get_oversized_payload(self):
        big = base64.urlsafe_b64encode(b"x" * 20_000).decode()
        client = TestClient(app)
        r = client.get(f"/import?src=immobiliare&v=1&p={big}")

        assert r.status_code == 400

    def test_import_get_malformed_base64(self):
        client = TestClient(app)
        r = client.get("/import?src=immobiliare&v=1&p=!!!invalid!!!")

        assert r.status_code == 400

    def test_import_get_invalid_source(self):
        client = TestClient(app)
        r = client.get("/import?src=unknown&v=1&p=xyz")

        assert r.status_code == 400

    def test_import_get_missing_payload(self):
        client = TestClient(app)
        r = client.get("/import?src=immobiliare&v=1&p=")

        assert r.status_code == 400

    def test_import_post_oversized_html(self):
        client = TestClient(app)
        r = client.post("/import", data={"src": "immobiliare", "html": "x" * 300_000})
        assert r.status_code == 400
