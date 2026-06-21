from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

from stimmo.data import amenities
from stimmo.data.amenities import (
    AmenityFetchError,
    _AmenityCache,
    _post_overpass,
    fetch_amenities,
)
from stimmo.models import AmenityScore
from stimmo.web.app import app


def _make_response(status: int, body: dict | None = None, headers: dict | None = None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.ok = status < 400
    r.headers = headers or {}
    if body is not None:
        r.json.return_value = body
    r.raise_for_status = MagicMock()
    return r


EMPTY_RESPONSE = {"elements": []}
OK_RESPONSE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 45.46, "lon": 9.19, "tags": {"station": "subway"}}
    ]
}


# ---------------------------------------------------------------------------
# _post_overpass retry scenarios
# ---------------------------------------------------------------------------


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_502_then_200_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [
        _make_response(502),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE
    assert mock_sleep.call_count == 1


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_502_then_502_raises(mock_post, mock_sleep):
    mock_post.side_effect = [_make_response(502), _make_response(502)]
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == 2


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_429_retry_after_honored(mock_post, mock_sleep):
    """429 with Retry-After within budget → retry once."""
    mock_post.side_effect = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE
    mock_sleep.assert_called_once_with(1.0)


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_429_retry_after_too_large_aborts(mock_post, mock_sleep):
    """429 with Retry-After exceeding budget → raises immediately."""
    mock_post.return_value = _make_response(429, headers={"Retry-After": "10"})
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == 1
    mock_sleep.assert_not_called()


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_soft_failure_remark_triggers_retry(mock_post, mock_sleep):
    """200 with runtime-error remark and no elements → retry."""
    soft_fail = {"remark": "runtime error: Query timed out", "elements": []}
    mock_post.side_effect = [
        _make_response(200, soft_fail),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE
    assert mock_sleep.call_count == 1


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_request_exception_then_ok(mock_post, mock_sleep):
    mock_post.side_effect = [
        requests.ConnectionError("refused"),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_request_exception_twice_raises(mock_post, mock_sleep):
    mock_post.side_effect = [
        requests.ConnectionError("refused"),
        requests.ConnectionError("refused"),
    ]
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == 2


# ---------------------------------------------------------------------------
# /api/amenities web endpoint
# ---------------------------------------------------------------------------


_CLIENT = TestClient(app)


def test_api_amenities_happy_path(monkeypatch):
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: amenities.AmenityScore())
    r = _CLIENT.get("/en/api/amenities", params={"lat": 45.464, "lon": 9.190})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "score" in body
    assert "html" in body


def test_api_amenities_failure_path(monkeypatch):
    def _raise(lat, lon):
        raise AmenityFetchError("timeout", attempts=2)

    monkeypatch.setattr(amenities, "fetch_amenities", _raise)
    r = _CLIENT.get("/en/api/amenities", params={"lat": 45.464, "lon": 9.190})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["attempts"] == 2
    assert "timeout" in body["error"]


# ---------------------------------------------------------------------------
# Step 6 — _AmenityCache TTL+LRU tests (§5.8)
# ---------------------------------------------------------------------------


class TestAmenityCache:
    """Unit tests for _AmenityCache with an injectable clock."""

    def _make_score(self, metro: int = 0) -> AmenityScore:
        return AmenityScore(metro_within_500m=metro)

    def test_get_miss_returns_none(self):
        cache = _AmenityCache(ttl_s=3600, clock=lambda: 1000.0)
        assert cache.get((45.464, 9.190)) is None

    def test_put_then_get_returns_value(self):
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=3600, clock=lambda: clock_val[0])
        key = (45.464, 9.190)
        score = self._make_score(metro=2)
        cache.put(key, score)
        result = cache.get(key)
        assert result is not None
        assert result.metro_within_500m == 2

    def test_expired_entry_returns_none(self):
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=60, clock=lambda: clock_val[0])
        key = (45.464, 9.190)
        cache.put(key, self._make_score(metro=3))
        # Advance clock past TTL.
        clock_val[0] = 1000.0 + 61.0
        assert cache.get(key) is None

    def test_within_ttl_returns_cached_value(self):
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=60, clock=lambda: clock_val[0])
        key = (45.464, 9.190)
        score = self._make_score(metro=5)
        cache.put(key, score)
        # Advance clock but stay within TTL.
        clock_val[0] = 1000.0 + 59.0
        result = cache.get(key)
        assert result is not None
        assert result.metro_within_500m == 5

    def test_lru_eviction_at_maxsize(self):
        cache = _AmenityCache(ttl_s=3600, maxsize=2, clock=lambda: 1000.0)
        cache.put((1.0, 1.0), self._make_score(metro=1))
        cache.put((2.0, 2.0), self._make_score(metro=2))
        # Third entry triggers eviction of the LRU entry — (1.0, 1.0).
        cache.put((3.0, 3.0), self._make_score(metro=3))
        assert cache.get((1.0, 1.0)) is None  # evicted
        assert cache.get((2.0, 2.0)) is not None
        assert cache.get((3.0, 3.0)) is not None

    def test_lru_access_updates_recency(self):
        cache = _AmenityCache(ttl_s=3600, maxsize=2, clock=lambda: 1000.0)
        cache.put((1.0, 1.0), self._make_score(metro=1))
        cache.put((2.0, 2.0), self._make_score(metro=2))
        # Access (1.0, 1.0) to make it most-recently-used.
        cache.get((1.0, 1.0))
        # Adding (3.0, 3.0) should evict (2.0, 2.0) (now LRU), not (1.0, 1.0).
        cache.put((3.0, 3.0), self._make_score(metro=3))
        assert cache.get((1.0, 1.0)) is not None
        assert cache.get((2.0, 2.0)) is None  # evicted
        assert cache.get((3.0, 3.0)) is not None


class TestFetchAmenitiesCache:
    """Integration tests for the cached fetch_amenities wrapper (§5.8).

    Invariant: two calls for coords in the same quantization cell within TTL
    must produce exactly ONE Overpass _query call.
    """

    def _make_sample_score(self) -> AmenityScore:
        return AmenityScore(metro_within_500m=1, score_pct=2.5)

    def test_cache_hit_avoids_second_query(self):
        """Two calls within TTL for the same quantized cell → 1 uncached call."""
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=3600, clock=lambda: clock_val[0])
        call_count = [0]

        def _spy_uncached(lat: float, lon: float) -> AmenityScore:
            call_count[0] += 1
            return self._make_sample_score()

        with patch.object(amenities, "_fetch_amenities_uncached", side_effect=_spy_uncached):
            r1 = fetch_amenities(45.464, 9.190, _cache=cache)
            r2 = fetch_amenities(45.464, 9.190, _cache=cache)

        assert call_count[0] == 1, "expected exactly 1 Overpass call; cache should serve second"
        assert r1.metro_within_500m == r2.metro_within_500m

    def test_cache_hit_for_nearby_coords_same_quantization_cell(self):
        """Coords within the same ~111 m grid cell coalesce to one query."""
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=3600, clock=lambda: clock_val[0])
        call_count = [0]

        def _spy_uncached(lat: float, lon: float) -> AmenityScore:
            call_count[0] += 1
            return self._make_sample_score()

        with patch.object(amenities, "_fetch_amenities_uncached", side_effect=_spy_uncached):
            # Both round to (45.464, 9.190) at 3dp.
            fetch_amenities(45.4641, 9.1901, _cache=cache)
            fetch_amenities(45.4642, 9.1902, _cache=cache)

        assert call_count[0] == 1

    def test_cache_miss_after_ttl_expiry_re_queries(self):
        """After TTL expiry a new Overpass call must be made."""
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=60, clock=lambda: clock_val[0])
        call_count = [0]

        def _spy_uncached(lat: float, lon: float) -> AmenityScore:
            call_count[0] += 1
            return self._make_sample_score()

        with patch.object(amenities, "_fetch_amenities_uncached", side_effect=_spy_uncached):
            fetch_amenities(45.464, 9.190, _cache=cache)
            # Advance past TTL.
            clock_val[0] = 1000.0 + 61.0
            fetch_amenities(45.464, 9.190, _cache=cache)

        assert call_count[0] == 2, "expected 2 Overpass calls (miss + re-query after TTL)"

    def test_different_quantization_cells_each_query(self):
        """Coordinates in distinct quantization cells produce separate queries."""
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=3600, clock=lambda: clock_val[0])
        call_count = [0]

        def _spy_uncached(lat: float, lon: float) -> AmenityScore:
            call_count[0] += 1
            return self._make_sample_score()

        with patch.object(amenities, "_fetch_amenities_uncached", side_effect=_spy_uncached):
            # These round to distinct cells at 3dp.
            fetch_amenities(45.464, 9.190, _cache=cache)
            fetch_amenities(45.465, 9.191, _cache=cache)

        assert call_count[0] == 2

    def test_quantization_key_is_3dp(self):
        """Verify the quantization grid: round(lat, 3) × round(lon, 3)."""
        # Coords that differ only in the 4th+ decimal should share a cell.
        clock_val = [1000.0]
        cache = _AmenityCache(ttl_s=3600, clock=lambda: clock_val[0])
        call_count = [0]

        def _spy_uncached(lat: float, lon: float) -> AmenityScore:
            call_count[0] += 1
            return self._make_sample_score()

        with patch.object(amenities, "_fetch_amenities_uncached", side_effect=_spy_uncached):
            fetch_amenities(45.46400, 9.19000, _cache=cache)
            fetch_amenities(45.46449, 9.19049, _cache=cache)  # same cell after round(_, 3)
            fetch_amenities(45.46450, 9.19050, _cache=cache)  # rounds to 45.465, 9.191 → new cell

        assert call_count[0] == 2  # first two share a cell; third is a new cell
