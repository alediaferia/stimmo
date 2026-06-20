"""Tests for the share-token encode/decode module and the share + OG image routes."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from stimmo.data import amenities, geocode
from stimmo.models import (
    AmenityScore,
    ConstructionEra,
    EnergyClass,
    Exposure,
    FineCondition,
    OmiCondition,
    Orientation,
    Outdoor,
    Property,
    PropertyType,
)
from stimmo.web import share as _share
from stimmo.web.app import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_property() -> Property:
    return Property(
        address="Piazza Duomo 1, Milano",
        surface_m2=80.0,
        property_type=PropertyType.CIVILI,
        omi_condition=OmiCondition.NORMALE,
        fine_condition=FineCondition.ABITABILE,
        floor=2,
        total_floors=5,
        has_lift=True,
        energy_class=EnergyClass.C,
        outdoor=Outdoor.BALCONY,
        has_box=False,
        construction_era=ConstructionEra.POSTWAR_BOOM,
        orientation=Orientation.MIXED,
        exposure=Exposure.STREET,
        has_second_bathroom=False,
        room_count=3,
        asking_price_eur=650_000.0,
    )


def _sample_amenity() -> AmenityScore:
    return AmenityScore(
        metro_within_500m=1,
        tram_within_500m=2,
        score_pct=8.5,
    )


# ---------------------------------------------------------------------------
# Token encode / decode round-trip
# ---------------------------------------------------------------------------


class TestShareTokenRoundTrip:
    def test_basic_round_trip(self):
        prop = _sample_property()
        amen = _sample_amenity()
        token = _share.encode(prop, 45.4642, 9.1900, amen)

        decoded_prop, lat, lon, decoded_amen = _share.decode(token)

        assert decoded_prop.address == prop.address
        assert decoded_prop.surface_m2 == prop.surface_m2
        assert decoded_prop.property_type == prop.property_type
        assert decoded_prop.omi_condition == prop.omi_condition
        assert decoded_prop.fine_condition == prop.fine_condition
        assert decoded_prop.floor == prop.floor
        assert decoded_prop.total_floors == prop.total_floors
        assert decoded_prop.has_lift == prop.has_lift
        assert decoded_prop.energy_class == prop.energy_class
        assert decoded_prop.outdoor == prop.outdoor
        assert decoded_prop.has_box == prop.has_box
        assert decoded_prop.construction_era == prop.construction_era
        assert decoded_prop.orientation == prop.orientation
        assert decoded_prop.asking_price_eur == prop.asking_price_eur
        assert decoded_prop.room_count == prop.room_count

        assert abs(lat - 45.4642) < 1e-5
        assert abs(lon - 9.1900) < 1e-5

        assert decoded_amen.metro_within_500m == amen.metro_within_500m
        assert decoded_amen.tram_within_500m == amen.tram_within_500m
        assert abs(decoded_amen.score_pct - amen.score_pct) < 1e-6

    def test_token_is_url_safe_ascii(self):
        token = _share.encode(_sample_property(), 45.4642, 9.1900, AmenityScore())
        assert token.isascii()
        assert " " not in token
        assert "+" not in token
        assert "/" not in token
        assert "=" not in token  # no padding

    def test_token_length_under_1500_chars(self):
        """Typical token must stay well under social-crawler truncation limits."""
        prop = _sample_property()
        prop.address = "Via Montenapoleone 10, 20121 Milano MI, Italy"
        token = _share.encode(prop, 45.4642, 9.1900, _sample_amenity())
        assert len(token) < 1500, f"Token too long: {len(token)} chars"

    def test_property_without_optional_fields(self):
        prop = Property(
            address="Via Roma 5, Milano",
            surface_m2=60.0,
            property_type=PropertyType.ECONOMICO,
            omi_condition=OmiCondition.SCADENTE,
            fine_condition=FineCondition.DA_RISTRUTTURARE,
            floor=0,
            total_floors=3,
            has_lift=False,
            energy_class=None,
            outdoor=Outdoor.NONE,
            has_box=False,
            construction_era=ConstructionEra.PRE_WAR,
            orientation=Orientation.NORTH,
            exposure=Exposure.INTERNAL_COURTYARD,
            has_second_bathroom=False,
            room_count=None,
            asking_price_eur=200_000.0,
        )
        token = _share.encode(prop, 45.47, 9.19, AmenityScore())
        decoded_prop, _, _, _ = _share.decode(token)
        assert decoded_prop.energy_class is None
        assert decoded_prop.room_count is None


# ---------------------------------------------------------------------------
# Token version / back-compat
# ---------------------------------------------------------------------------


class TestShareTokenVersion:
    def test_wrong_version_byte_raises(self):
        prop = _sample_property()
        token = _share.encode(prop, 45.4642, 9.1900, AmenityScore())

        import base64

        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        # Replace version byte \x01 with \x02
        tampered = b"\x02" + raw[1:]
        bad_token = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
        with pytest.raises(_share.ShareTokenError, match="version"):
            _share.decode(bad_token)


# ---------------------------------------------------------------------------
# Malformed token handling
# ---------------------------------------------------------------------------


class TestShareTokenMalformed:
    def test_empty_string_raises(self):
        with pytest.raises(_share.ShareTokenError):
            _share.decode("")

    def test_garbage_base64_raises(self):
        with pytest.raises(_share.ShareTokenError):
            _share.decode("!!notbase64!!")

    def test_short_token_raises(self):
        import base64

        bad = base64.urlsafe_b64encode(b"\x01").rstrip(b"=").decode()
        with pytest.raises(_share.ShareTokenError):
            _share.decode(bad)

    def test_corrupt_zlib_raises(self):
        import base64

        # version byte + garbage compressed data
        bad_bytes = b"\x01" + b"\xff\xfe\xfd" * 10
        bad_token = base64.urlsafe_b64encode(bad_bytes).rstrip(b"=").decode()
        with pytest.raises(_share.ShareTokenError, match="Decompression failed"):
            _share.decode(bad_token)

    def test_valid_zlib_wrong_json_raises(self):
        import base64
        import zlib

        bad_bytes = b"\x01" + zlib.compress(b"not json at all")
        bad_token = base64.urlsafe_b64encode(bad_bytes).rstrip(b"=").decode()
        with pytest.raises(_share.ShareTokenError, match="JSON parse error"):
            _share.decode(bad_token)

    def test_valid_json_wrong_version_raises(self):
        import base64
        import json
        import zlib

        payload = json.dumps({"v": 99, "p": {}, "lat": 0, "lon": 0, "a": {}}).encode()
        bad_bytes = b"\x01" + zlib.compress(payload)
        bad_token = base64.urlsafe_b64encode(bad_bytes).rstrip(b"=").decode()
        with pytest.raises(_share.ShareTokenError, match="version mismatch"):
            _share.decode(bad_token)


# ---------------------------------------------------------------------------
# Share route (GET /{lang}/s/{token})
# ---------------------------------------------------------------------------


class TestShareRoute:
    # Coordinates 45.4642, 9.1900 → zone B12 (CENTRO STORICO) in the bundled data,
    # which has CIVILI/NORMALE — use real zone lookup (no monkeypatch needed).
    def _make_token(self) -> str:
        return _share.encode(_sample_property(), 45.4642, 9.1900, AmenityScore())

    def test_share_route_renders_result_html(self):
        token = self._make_token()
        client = TestClient(app)
        r = client.get(f"/en/s/{token}")
        assert r.status_code == 200
        body = r.text
        # Should render result.html content
        assert "Report" in body
        verdicts = ("Verdict · Under-priced", "Verdict · Fair", "Verdict · Over-priced")
        assert any(v in body for v in verdicts)

    def test_share_route_contains_og_meta(self):
        token = self._make_token()
        client = TestClient(app)
        r = client.get(f"/en/s/{token}")
        assert r.status_code == 200
        body = r.text
        assert 'property="og:title"' in body
        assert 'property="og:image"' in body
        assert "/og/" in body
        assert ".png" in body

    def test_share_route_malformed_token_returns_error(self):
        client = TestClient(app)
        r = client.get("/en/s/BADTOKEN!!!")
        # FastAPI may reject the path chars — either 400 or 422 are acceptable
        assert r.status_code in (400, 404, 422)

    def test_share_route_garbage_token_renders_error_page(self):
        import base64
        import zlib

        # Construct a syntactically valid but semantically bad token
        bad_bytes = b"\x01" + zlib.compress(b"garbage json")
        bad_token = base64.urlsafe_b64encode(bad_bytes).rstrip(b"=").decode()

        client = TestClient(app)
        r = client.get(f"/en/s/{bad_token}")
        # Should get a 400 error page (not a 500)
        assert r.status_code == 400

    def test_share_route_italian(self):
        token = self._make_token()
        client = TestClient(app)
        r = client.get(f"/it/s/{token}")
        assert r.status_code == 200
        assert "Report" in r.text


# ---------------------------------------------------------------------------
# OG image route (GET /og/{token}.png)
# ---------------------------------------------------------------------------


class TestOgImageRoute:
    # Same coordinate logic: 45.4642, 9.1900 → B12, which has CIVILI/NORMALE.
    def _make_token(self) -> str:
        return _share.encode(_sample_property(), 45.4642, 9.1900, AmenityScore())

    def test_og_image_returns_png(self):
        token = self._make_token()
        client = TestClient(app)
        r = client.get(f"/og/{token}.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        # PNG magic bytes
        assert r.content[:4] == b"\x89PNG"

    def test_og_image_is_valid_png(self):
        from PIL import Image

        token = self._make_token()
        client = TestClient(app)
        r = client.get(f"/og/{token}.png")
        assert r.status_code == 200

        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1200, 630)
        assert img.mode == "RGB"

    def test_og_image_has_cache_header(self):
        token = self._make_token()
        client = TestClient(app)
        r = client.get(f"/og/{token}.png")
        assert r.status_code == 200
        assert "max-age" in r.headers.get("cache-control", "")

    def test_og_image_bad_token_returns_404(self):
        client = TestClient(app)
        r = client.get("/og/BADTOKEN.png")
        assert r.status_code == 404

    def test_og_image_garbage_token_returns_404(self):
        import base64
        import zlib

        bad_bytes = b"\x01" + zlib.compress(b"garbage")
        bad_token = base64.urlsafe_b64encode(bad_bytes).rstrip(b"=").decode()

        client = TestClient(app)
        r = client.get(f"/og/{bad_token}.png")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# OG card font: the bundled webfont woff2 is a per-page subset (only 'A' from
# basic Latin); the card must carry its own complete font or every label renders
# as tofu/.notdef.  These guard against silently shipping an incomplete font.
# ---------------------------------------------------------------------------


class TestOgImageFont:
    REQUIRED = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789€²·Δ×"

    def test_bundled_font_covers_required_glyphs(self):
        from fontTools.ttLib import TTFont

        from stimmo.web import ogimage

        cmap = TTFont(str(ogimage._FONT_PATH)).getBestCmap()
        missing = [c for c in self.REQUIRED if ord(c) not in cmap]
        assert not missing, f"OG font missing glyphs: {''.join(missing)!r}"

    def test_render_produces_visible_ink(self):
        # A card that renders no glyphs is just background + the verdict stripe.
        # Assert the dark ink colour used for the headline/stats is actually drawn.
        from PIL import Image

        from stimmo.data import omi, zones
        from stimmo.valuation import engine
        from stimmo.web import ogimage

        prop = _sample_property()
        zone_code, _zn = zones.zone_for_point(45.4642, 9.1900)
        quote = omi.lookup(zone_code, prop.property_type, prop.omi_condition)
        est = engine.estimate(prop, quote, AmenityScore())

        png = ogimage.render(est, prop)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        ink_pixels = dict(
            (color, count) for count, color in img.getcolors(maxcolors=1_000_000)
        ).get(ogimage._INK, 0)
        assert ink_pixels > 500, f"too few ink pixels ({ink_pixels}); text not rendering"


# ---------------------------------------------------------------------------
# Estimate POST injects share_url into result
# ---------------------------------------------------------------------------


class TestEstimateShareUrl:
    def test_estimate_result_contains_share_url(self, monkeypatch):
        monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
        monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

        client = TestClient(app)
        data = {
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
        r = client.post("/en/estimate", data=data)
        assert r.status_code == 200
        body = r.text
        # Share button with /s/ link should appear
        assert "/s/" in body
        # OG image URL with /og/ should appear in meta tags
        assert "/og/" in body


# ---------------------------------------------------------------------------
# stimmo_share_events_total counter (invalid tokens are served as 200/404 and
# would otherwise be invisible in the HTTP metrics)
# ---------------------------------------------------------------------------


class TestShareMetrics:
    @staticmethod
    def _count(event: str, outcome: str) -> float:
        from prometheus_client import REGISTRY

        return (
            REGISTRY.get_sample_value(
                "stimmo_share_events_total", {"event": event, "outcome": outcome}
            )
            or 0.0
        )

    def test_open_ok_increments(self):
        token = _share.encode(_sample_property(), 45.4642, 9.1900, AmenityScore())
        before = self._count("open", "ok")
        TestClient(app).get(f"/en/s/{token}")
        assert self._count("open", "ok") == before + 1

    def test_open_invalid_increments(self):
        before = self._count("open", "invalid")
        # Bad tokens render the error page (400), indistinguishable in the HTTP
        # counter from a valid-token recompute failure — hence the outcome split.
        r = TestClient(app).get("/en/s/BADTOKEN")
        assert r.status_code == 400
        assert self._count("open", "invalid") == before + 1

    def test_og_render_ok_increments(self):
        token = _share.encode(_sample_property(), 45.4642, 9.1900, AmenityScore())
        before = self._count("og_render", "ok")
        TestClient(app).get(f"/og/{token}.png")
        assert self._count("og_render", "ok") == before + 1

    def test_og_render_invalid_increments(self):
        before = self._count("og_render", "invalid")
        r = TestClient(app).get("/og/BADTOKEN.png")
        assert r.status_code == 404
        assert self._count("og_render", "invalid") == before + 1
