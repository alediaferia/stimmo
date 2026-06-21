"""Tests for the share-token encode/decode module and the share + OG image routes."""

from __future__ import annotations

import base64
import io
import zlib

import pytest
from fastapi.testclient import TestClient

from stimmo.data import amenities, geocode
from stimmo.models import (
    AmenityItem,
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
from stimmo.web.share_store import SqliteShareStore

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


# ---------------------------------------------------------------------------
# Step 0 — payload/blob primitives: legacy byte-identity
# ---------------------------------------------------------------------------


class TestPayloadBlobPrimitives:
    """encode() must produce the exact same bytes as before the refactor.

    The fixed fixture captures the token format (version byte + zlib blob) so
    we can assert the refactored primitives produce the same compressed blob.
    """

    def test_legacy_encode_uses_primitives_byte_identical(self):
        """encode() == b'\\x01' + _compress_payload(_payload_dict(...))."""
        prop = _sample_property()
        amen = _sample_amenity()
        lat, lon = 45.4642, 9.1900

        # Produce token via the public API.
        token = _share.encode(prop, lat, lon, amen)
        token_bytes = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))

        # Reproduce via primitives.
        payload = _share._payload_dict(prop, lat, lon, amen)
        blob_from_primitives = _share._compress_payload(payload)

        assert token_bytes == b"\x01" + blob_from_primitives

    def test_compress_decompress_round_trip(self):
        prop = _sample_property()
        amen = _sample_amenity()
        payload = _share._payload_dict(prop, 45.4642, 9.1900, amen)
        blob = _share._compress_payload(payload)
        recovered = _share._decompress_payload(blob)
        assert recovered["v"] == 1
        assert recovered["lat"] == round(45.4642, 6)
        assert recovered["lon"] == round(9.1900, 6)

    def test_payload_dict_excludes_items_within_500m(self):
        prop = _sample_property()
        amen = AmenityScore(
            metro_within_500m=1,
            items_within_500m=[
                AmenityItem(kind="metro", name="Duomo M3", lat=45.464, lon=9.190)
            ],
        )
        payload = _share._payload_dict(prop, 45.4642, 9.1900, amen)
        assert "items_within_500m" not in payload.get("a", {})

    def test_legacy_decode_still_round_trips(self):
        """Full legacy encode → decode round-trip is unchanged after refactor."""
        prop = _sample_property()
        amen = _sample_amenity()
        token = _share.encode(prop, 45.4642, 9.1900, amen)
        p2, lat2, _lon2, a2 = _share.decode(token)
        assert p2.address == prop.address
        assert abs(lat2 - 45.4642) < 1e-9
        assert a2.metro_within_500m == amen.metro_within_500m

    def test_legacy_token_has_no_m_key(self):
        """Legacy encode must NOT add the 'm' marker key (keeps token small, Q5)."""
        prop = _sample_property()
        amen = AmenityScore(
            metro_within_500m=2,
            items_within_500m=[
                AmenityItem(kind="metro", name="Cadorna", lat=45.466, lon=9.178)
            ],
        )
        token = _share.encode(prop, 45.4642, 9.1900, amen)
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = _share._decompress_payload(raw[1:])  # skip version byte
        assert "m" not in payload


# ---------------------------------------------------------------------------
# Step 1 — encode_blob / decode_blob with "m" markers
# ---------------------------------------------------------------------------


class TestEncodeDecodeBlob:
    def _make_amen_with_items(self) -> AmenityScore:
        return AmenityScore(
            metro_within_500m=1,
            tram_within_500m=1,
            score_pct=6.0,
            items_within_500m=[
                AmenityItem(kind="metro", name="Duomo M1/M3", lat=45.4641, lon=9.1899),
                AmenityItem(kind="tram", name="Via Torino", lat=45.4635, lon=9.1901),
                AmenityItem(kind="park", name=None, lat=45.4650, lon=9.1920),
            ],
        )

    def test_encode_blob_returns_bytes(self):
        prop = _sample_property()
        amen = self._make_amen_with_items()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        assert isinstance(blob, bytes)
        assert len(blob) > 0

    def test_encode_blob_has_no_version_byte(self):
        """Stored blob is raw zlib — no b'\\x01' prefix (the store is codec-agnostic)."""
        prop = _sample_property()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, AmenityScore())
        # Should be valid zlib (decompress without stripping a version byte).
        decompressed = zlib.decompress(blob)
        import json

        payload = json.loads(decompressed)
        assert payload.get("v") == 1

    def test_decode_blob_round_trips_property(self):
        prop = _sample_property()
        amen = self._make_amen_with_items()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        p2, lat2, lon2, _a2 = _share.decode_blob(blob)
        assert p2.address == prop.address
        assert p2.surface_m2 == prop.surface_m2
        assert abs(lat2 - 45.4642) < 1e-9
        assert abs(lon2 - 9.1900) < 1e-9

    def test_decode_blob_markers_present_and_names_absent(self):
        """items_within_500m should be populated with kind+coords; name must be None."""
        prop = _sample_property()
        amen = self._make_amen_with_items()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        _, _, _, a2 = _share.decode_blob(blob)
        items = a2.items_within_500m
        assert len(items) == 3
        for item in items:
            assert item.name is None  # names omitted (D6)
        kinds = {i.kind for i in items}
        assert "metro" in kinds
        assert "tram" in kinds
        assert "park" in kinds

    def test_decode_blob_marker_coords_preserved(self):
        prop = _sample_property()
        amen = AmenityScore(
            items_within_500m=[
                AmenityItem(kind="pharmacy", name="Farmacia", lat=45.4610, lon=9.1950)
            ]
        )
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        _, _, _, a2 = _share.decode_blob(blob)
        assert len(a2.items_within_500m) == 1
        item = a2.items_within_500m[0]
        assert item.kind == "pharmacy"
        assert abs(item.lat - 45.4610) < 1e-5
        assert abs(item.lon - 9.1950) < 1e-5

    def test_decode_blob_more_than_12_capped(self):
        """Items beyond the 12-marker cap must be dropped."""
        prop = _sample_property()
        items = [
            AmenityItem(kind="metro", name=f"Station {i}", lat=45.46 + i * 0.001, lon=9.19)
            for i in range(20)
        ]
        amen = AmenityScore(metro_within_500m=20, items_within_500m=items)
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        _, _, _, a2 = _share.decode_blob(blob)
        assert len(a2.items_within_500m) <= 12

    def test_encode_blob_caps_markers_at_12(self):
        """encode_blob must only include the first 12 items in the 'm' key."""
        import json

        prop = _sample_property()
        items = [
            AmenityItem(kind="tram", name=f"Stop {i}", lat=45.46 + i * 0.001, lon=9.19)
            for i in range(15)
        ]
        amen = AmenityScore(tram_within_500m=15, items_within_500m=items)
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        payload = json.loads(zlib.decompress(blob))
        assert len(payload["m"]) == 12

    def test_decode_blob_no_markers(self):
        """Blobs without 'm' key (e.g. from encode_blob with empty items) decode fine."""
        prop = _sample_property()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, AmenityScore())
        _, _, _, a2 = _share.decode_blob(blob)
        assert a2.items_within_500m == []

    def test_decode_blob_amen_counters_preserved(self):
        """AmenityScore counters and score_pct must survive the encode_blob/decode_blob cycle."""
        prop = _sample_property()
        amen = AmenityScore(metro_within_500m=3, tram_within_500m=5, score_pct=7.5)
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        _, _, _, a2 = _share.decode_blob(blob)
        assert a2.metro_within_500m == 3
        assert a2.tram_within_500m == 5
        assert abs(a2.score_pct - 7.5) < 1e-6


# ---------------------------------------------------------------------------
# Step 1 — kind order (§5.3)
# ---------------------------------------------------------------------------


class TestMarkerKindOrder:
    def test_kind_indices_match_spec(self):
        """Verify the fixed kind order matches §5.3."""
        assert _share._MARKER_KINDS == (
            "metro", "tram", "park", "supermarket", "school", "pharmacy"
        )

    def test_round_trip_all_kinds(self):
        """All six kinds survive encode_blob → decode_blob."""
        prop = _sample_property()
        items = [
            AmenityItem(kind="metro", name="x", lat=45.461, lon=9.190),
            AmenityItem(kind="tram", name="x", lat=45.462, lon=9.191),
            AmenityItem(kind="park", name="x", lat=45.463, lon=9.192),
            AmenityItem(kind="supermarket", name="x", lat=45.464, lon=9.193),
            AmenityItem(kind="school", name="x", lat=45.465, lon=9.194),
            AmenityItem(kind="pharmacy", name="x", lat=45.466, lon=9.195),
        ]
        amen = AmenityScore(items_within_500m=items)
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        _, _, _, a2 = _share.decode_blob(blob)
        recovered_kinds = {i.kind for i in a2.items_within_500m}
        assert recovered_kinds == {"metro", "tram", "park", "supermarket", "school", "pharmacy"}


# ---------------------------------------------------------------------------
# Step 0 — resolve() helper
# ---------------------------------------------------------------------------


class TestResolve:
    """Tests for share.resolve() dual-path logic (§5.5)."""

    def _make_store(self) -> SqliteShareStore:
        return SqliteShareStore(":memory:")

    def test_resolve_store_path(self):
        """A short alnum id stored in the store resolves via decode_blob."""
        prop = _sample_property()
        amen = AmenityScore(metro_within_500m=1)
        store = self._make_store()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        id_ = store.put(blob)

        p2, lat2, _lon2, a2 = _share.resolve(id_, store)
        assert p2.address == prop.address
        assert abs(lat2 - 45.4642) < 1e-9
        assert a2.metro_within_500m == 1

    def test_resolve_legacy_path(self):
        """A long legacy token resolves via decode() even with an empty store."""
        prop = _sample_property()
        amen = _sample_amenity()
        token = _share.encode(prop, 45.4642, 9.1900, amen)
        store = self._make_store()

        p2, _lat2, _lon2, _a2 = _share.resolve(token, store)
        assert p2.address == prop.address

    def test_resolve_invalid_raises(self):
        """Neither store nor legacy path resolves a garbage id."""
        store = self._make_store()
        with pytest.raises(_share.ShareTokenError):
            _share.resolve("ZZZZZZZZ", store)

    def test_resolve_store_miss_falls_back_to_legacy(self):
        """A short-looking alnum id not in the store falls back to legacy decode.
        If the fallback also fails it raises ShareTokenError (not a silent miss)."""
        store = self._make_store()
        # 8-char alnum id that is NOT in the store AND is not a valid legacy token.
        with pytest.raises(_share.ShareTokenError):
            _share.resolve("ABCDEFGH", store)

    def test_resolve_legacy_long_token_still_resolves(self):
        """A pre-existing legacy long token must still resolve after the store is wired in."""
        prop = _sample_property()
        token = _share.encode(prop, 45.4642, 9.1900, AmenityScore())
        store = self._make_store()
        # Token is long and contains base64url chars (dashes, underscores), so
        # resolve() falls back to the legacy decode() path.
        p2, _, _, _ = _share.resolve(token, store)
        assert p2.address == prop.address


# ---------------------------------------------------------------------------
# Step 3 — /s/{id} short route
# ---------------------------------------------------------------------------


class TestShareViewShort:
    """Tests for the new lang-free GET /s/{id} route."""

    def _make_stored_id(self) -> str:
        """Store a valid blob and return its short id."""
        from stimmo.web.app import _share_store as _store

        prop = _sample_property()
        amen = AmenityScore()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        return _store.put(blob)

    def test_short_route_renders_result_html(self):
        id_ = self._make_stored_id()
        client = TestClient(app)
        r = client.get(f"/s/{id_}")
        assert r.status_code == 200
        body = r.text
        # Result page should contain the report heading and a verdict label.
        assert "Report" in body
        assert "Verdetto" in body or "Verdict" in body

    def test_short_route_id_length_lte_10(self):
        """The share_url emitted by the write path must be …/s/<≤10 char id>."""
        from stimmo.web.app import _share_store as _store

        prop = _sample_property()
        amen = AmenityScore()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        id_ = _store.put(blob)
        assert len(id_) <= 10

    def test_short_route_bad_id_returns_400(self):
        client = TestClient(app)
        r = client.get("/s/NOTEXIST1")
        assert r.status_code == 400

    def test_short_route_locale_negotiation_italian_cookie(self):
        """stimmo_lang=it cookie → Italian result page."""
        id_ = self._make_stored_id()
        client = TestClient(app, cookies={"stimmo_lang": "it"})
        r = client.get(f"/s/{id_}")
        assert r.status_code == 200
        # Italian verdict labels should be present.
        assert "Verdetto" in r.text

    def test_short_route_locale_negotiation_english_cookie(self):
        """stimmo_lang=en cookie → English result page."""
        id_ = self._make_stored_id()
        client = TestClient(app, cookies={"stimmo_lang": "en"})
        r = client.get(f"/s/{id_}")
        assert r.status_code == 200
        assert "Verdict" in r.text

    def test_short_route_locale_negotiation_accept_language(self):
        """Accept-Language: en → English result page (no cookie present)."""
        id_ = self._make_stored_id()
        client = TestClient(app)
        r = client.get(f"/s/{id_}", headers={"Accept-Language": "en-US,en;q=0.9"})
        assert r.status_code == 200
        assert "Verdict" in r.text

    def test_short_route_locale_fallback_italian(self):
        """No cookie, no Accept-Language → it_IT default."""
        id_ = self._make_stored_id()
        client = TestClient(app)
        r = client.get(f"/s/{id_}")
        assert r.status_code == 200
        # Italian is the default locale
        assert "Verdetto" in r.text

    def test_short_route_no_lang_prefix_in_url(self, monkeypatch):
        """The share_url produced by the estimate route must NOT contain /{lang}/s/."""
        import re

        import stimmo.web.app as _app
        from stimmo.data import amenities as _amen_mod
        from stimmo.data import geocode as _gc

        old_store = _app._share_store
        try:
            _app._share_store = SqliteShareStore(":memory:")
            monkeypatch.setattr(_gc, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
            monkeypatch.setattr(_amen_mod, "fetch_amenities", lambda lat, lon: AmenityScore())
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
            # The share URL should be /s/<id>, NOT /en/s/<token>
            assert "/s/" in body
            # Ensure the share_url does NOT have /en/s/ or /it/s/ (lang prefix)
            lang_prefixed = re.search(r"/[a-z]{2}/s/", body)
            assert lang_prefixed is None, f"Found lang-prefixed share URL: {lang_prefixed.group()}"
        finally:
            _app._share_store = old_store


# ---------------------------------------------------------------------------
# Step 3 — OG image route resolves both store ids and legacy tokens
# ---------------------------------------------------------------------------


class TestOgImageDualPath:
    def test_og_image_with_store_id(self):
        """OG route must accept a short store id."""
        from stimmo.web.app import _share_store as _store

        prop = _sample_property()
        amen = AmenityScore()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, amen)
        id_ = _store.put(blob)
        client = TestClient(app)
        r = client.get(f"/og/{id_}.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    def test_og_image_with_legacy_token_still_works(self):
        """OG route must still accept the legacy long token (D5)."""
        token = _share.encode(_sample_property(), 45.4642, 9.1900, AmenityScore())
        client = TestClient(app)
        r = client.get(f"/og/{token}.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"


# ---------------------------------------------------------------------------
# Step 3 — SHARE_RESOLVE metrics
# ---------------------------------------------------------------------------


class TestShareResolveMetrics:
    @staticmethod
    def _resolve_count(path: str) -> float:
        from prometheus_client import REGISTRY

        return (
            REGISTRY.get_sample_value("stimmo_share_resolve_total", {"path": path}) or 0.0
        )

    def test_store_path_increments_store_counter(self):
        from stimmo.web.app import _share_store as _store

        prop = _sample_property()
        blob = _share.encode_blob(prop, 45.4642, 9.1900, AmenityScore())
        id_ = _store.put(blob)
        before = self._resolve_count("store")
        TestClient(app).get(f"/s/{id_}")
        assert self._resolve_count("store") == before + 1

    def test_legacy_path_increments_legacy_counter(self):
        """A long legacy token resolved via /s/<token> should increment 'legacy' path."""
        token = _share.encode(_sample_property(), 45.4642, 9.1900, AmenityScore())
        before = self._resolve_count("legacy")
        r = TestClient(app).get(f"/s/{token}")
        assert r.status_code == 200
        assert self._resolve_count("legacy") == before + 1

    def test_miss_increments_miss_counter(self):
        """An unknown id on the /s/ route should increment the 'miss' counter."""
        before = self._resolve_count("miss")
        r = TestClient(app).get("/s/NOTEXIST1")
        assert r.status_code == 400
        assert self._resolve_count("miss") == before + 1


# ---------------------------------------------------------------------------
# Step 3 — write path emits /s/<id> and /og/<id>.png (no lang prefix)
# ---------------------------------------------------------------------------


class TestWritePathUrls:
    """Verify the URLs emitted by _build_result_context after Step 3."""

    def test_estimate_emits_short_share_url(self, monkeypatch):
        import re

        import stimmo.web.app as _app

        monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
        monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

        old_store = _app._share_store
        try:
            _app._share_store = SqliteShareStore(":memory:")
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
            # Must have /s/ link
            assert "/s/" in body
            # Must have /og/ link
            assert "/og/" in body
            # Confirm the /s/<id> id length is ≤ 10 chars by extracting it
            match = re.search(r"/s/([A-Za-z0-9]+)", body)
            assert match is not None
            assert len(match.group(1)) <= 10
        finally:
            _app._share_store = old_store
