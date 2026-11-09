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
# _post_overpass: one attempt per endpoint, no sleeping, mirror within the budget
# ---------------------------------------------------------------------------


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_502_primary_falls_through_to_mirror_without_sleeping(mock_post, mock_sleep):
    mock_post.side_effect = [_make_response(502), _make_response(200, OK_RESPONSE)]
    assert _post_overpass("dummy") == OK_RESPONSE
    urls = [c.args[0] for c in mock_post.call_args_list]
    assert urls == list(amenities.OVERPASS_ENDPOINTS)
    mock_sleep.assert_not_called()


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_all_endpoints_failing_raises_with_one_attempt_each(mock_post, mock_sleep):
    mock_post.side_effect = [_make_response(502), _make_response(503)]
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == len(amenities.OVERPASS_ENDPOINTS)
    assert mock_post.call_count == 2
    mock_sleep.assert_not_called()


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_429_is_not_retried_or_slept_on(mock_post, mock_sleep):
    mock_post.side_effect = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, OK_RESPONSE),
    ]
    assert _post_overpass("dummy") == OK_RESPONSE
    mock_sleep.assert_not_called()


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_soft_failure_remark_moves_to_mirror(mock_post, mock_sleep):
    soft_fail = {"remark": "runtime error: Query timed out", "elements": []}
    mock_post.side_effect = [_make_response(200, soft_fail), _make_response(200, OK_RESPONSE)]
    assert _post_overpass("dummy") == OK_RESPONSE
    mock_sleep.assert_not_called()


@patch("stimmo.data.amenities.requests.post")
def test_per_call_timeout_is_short(mock_post):
    mock_post.return_value = _make_response(200, OK_RESPONSE)
    _post_overpass("dummy")
    assert mock_post.call_args.kwargs["timeout"] <= amenities._TIMEOUT <= 5


@patch("stimmo.data.amenities.requests.post")
def test_mirror_skipped_when_budget_is_spent(mock_post):
    mock_post.side_effect = requests.ConnectionError("refused")
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy", deadline=amenities.time.monotonic() - 1)
    assert exc_info.value.attempts == 1
    assert mock_post.call_count == 1


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_hanging_overpass_gives_up_within_the_budget(mock_post, mock_sleep):
    """Both endpoints time out: one attempt each, no sleep, and the timeouts handed to
    requests add up to well under the old ~42 s per query."""
    mock_post.side_effect = requests.ReadTimeout("read timed out")
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == 2
    mock_sleep.assert_not_called()
    assert sum(c.kwargs["timeout"] for c in mock_post.call_args_list) <= 10


@patch("stimmo.data.amenities.requests.post")
def test_upstream_histogram_records_timeout(mock_post):
    from stimmo.web import metrics

    def _count(outcome):
        return metrics.UPSTREAM_DURATION.labels(upstream="overpass", outcome=outcome)._sum.get()

    before = _count("timeout")
    mock_post.side_effect = requests.ReadTimeout("slow")
    with pytest.raises(AmenityFetchError):
        _post_overpass("dummy")
    sample = [
        s
        for m in metrics.UPSTREAM_DURATION.collect()
        for s in m.samples
        if s.name.endswith("_count") and s.labels == {"upstream": "overpass", "outcome": "timeout"}
    ]
    assert sample and sample[0].value >= 2
    assert _count("timeout") >= before


# ---------------------------------------------------------------------------
# Merged single-query path == the old two-query path
# ---------------------------------------------------------------------------

_LAT, _LON = 45.4642, 9.1900


def _offset(north_m: float, east_m: float) -> tuple[float, float]:
    return (
        _LAT + north_m / 111_195.0,
        _LON + east_m / (111_195.0 * amenities.math.cos(amenities.math.radians(_LAT))),
    )


def _node(i: int, tags: dict, north: float, east: float) -> dict:
    lat, lon = _offset(north, east)
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags}


def _way(i: int, tags: dict, north: float, east: float, n: int = 25, half: float = 40) -> dict:
    pts = []
    for k in range(n):
        ang = 2 * amenities.math.pi * k / n
        la, lo = _offset(
            north + half * amenities.math.sin(ang), east + half * amenities.math.cos(ang)
        )
        pts.append({"lat": la, "lon": lo})
    return {"type": "way", "id": i, "tags": tags, "geometry": pts}


_SUBWAY = {"railway": "station", "station": "subway"}
_FIXTURE_ELEMENTS = [
    _node(1, _SUBWAY, 200, 100),  # near
    _node(2, _SUBWAY, 700, 100),  # ring
    _node(3, {"railway": "tram_stop"}, 100, -300),
    _node(4, {"railway": "tram_stop"}, -800, 200),
    _node(5, {"railway": "tram_stop"}, -850, 250),
    _way(6, {"leisure": "park"}, 300, 300),  # near, large
    _way(7, {"leisure": "park"}, 0, 800),  # ring
    _way(8, {"leisure": "park"}, 900, 0, n=10),  # ring, too few vertices: not counted at all
    _node(9, {"shop": "supermarket"}, 50, 50),
    _way(10, {"shop": "supermarket"}, -400, 0, n=8),
    _node(11, {"shop": "supermarket"}, 0, -950),
    _node(12, {"amenity": "school", "name": "Scuola A"}, 450, 0),
    _node(13, {"amenity": "school"}, 520, 0),
    _node(14, {"amenity": "school"}, -600, 600),
    _node(15, {"amenity": "pharmacy"}, 480, 40),
    _node(16, {"amenity": "pharmacy"}, 100, 990 - 100),
    _node(17, {"amenity": "pharmacy"}, 1500, 0),  # beyond 1000: the server would not return it
]


def _fake_overpass(radius: int):
    """What Overpass would return for ``around:radius`` over the fixture."""
    out = []
    for el in _FIXTURE_ELEMENTS:
        if "lat" in el:
            d = amenities._haversine_m(_LAT, _LON, el["lat"], el["lon"])
        else:
            d = min(amenities._haversine_m(_LAT, _LON, p["lat"], p["lon"]) for p in el["geometry"])
        if d <= radius:
            out.append(el)
    return {"elements": out}


def _legacy_two_query_score(lat, lon):
    """The pre-merge implementation, kept verbatim as the reference."""
    data_500 = _fake_overpass(500)
    counts_500 = amenities._count_elements(data_500)
    counts_1000 = amenities._count_elements(_fake_overpass(1000))
    ring = {k: max(0, counts_1000.get(k, 0) - counts_500.get(k, 0)) for k in amenities.SCORE_CAPS}
    raw = 0.0
    for kind in amenities.SCORE_CAPS:
        raw += amenities._score_for_counts(kind, counts_500.get(kind, 0), weight=1.0)
        raw += amenities._score_for_counts(kind, ring.get(kind, 0), weight=0.5)
    return counts_500, ring, round(min(raw, amenities.TOTAL_CAP_PCT), 2), data_500


def test_merged_query_matches_legacy_two_query_path():
    counts_500, ring, score_pct, data_500 = _legacy_two_query_score(_LAT, _LON)
    with patch.object(amenities, "_post_overpass", return_value=_fake_overpass(1000)) as post:
        got = amenities._fetch_amenities_uncached(_LAT, _LON)
    assert post.call_count == 1  # ONE query, at the outer radius
    assert "around:1000" in post.call_args.args[0]
    assert got.metro_within_500m == counts_500.get("metro", 0) == 1
    assert got.tram_within_500m == counts_500.get("tram", 0)
    assert got.parks_large_within_500m == counts_500.get("park", 0)
    assert got.supermarkets_within_500m == counts_500.get("supermarket", 0)
    assert got.schools_within_500m == counts_500.get("school", 0)
    assert got.pharmacies_within_500m == counts_500.get("pharmacy", 0)
    assert got.metro_500_1000m == ring["metro"] == 1
    assert got.tram_500_1000m == ring["tram"]
    assert got.parks_large_500_1000m == ring["park"]
    assert got.supermarkets_500_1000m == ring["supermarket"]
    assert got.schools_500_1000m == ring["school"]
    assert got.pharmacies_500_1000m == ring["pharmacy"]
    assert got.score_pct == score_pct
    assert got.items_within_500m == amenities._extract_items(data_500)


# ---------------------------------------------------------------------------
# resolve(): bounded wait, background completion, failure memo
# ---------------------------------------------------------------------------


def test_resolve_defers_when_fetch_outlives_budget_then_warms_cache(monkeypatch):
    import threading

    release = threading.Event()

    def slow(lat, lon):
        release.wait(5)
        return AmenityScore(metro_within_500m=2, score_pct=2.0)

    monkeypatch.setattr(amenities, "fetch_amenities", slow)
    t0 = amenities.time.monotonic()
    status, score, err = amenities.resolve(45.1, 9.1, 0.2)
    assert status == "deferred" and score is None and err is None
    assert amenities.time.monotonic() - t0 < 1.0
    assert amenities.peek(45.1, 9.1)[0] == "pending"
    release.set()
    for _ in range(100):
        state, got, _ = amenities.peek(45.1, 9.1)
        if state == "ok":
            break
        amenities.time.sleep(0.02)
    assert state == "ok" and got.metro_within_500m == 2
    assert amenities.resolve(45.1, 9.1, 0.2)[0] == "cached"


def test_resolve_failure_is_memoised_briefly(monkeypatch):
    calls = []

    def boom(lat, lon):
        calls.append(1)
        raise AmenityFetchError("down", attempts=2)

    monkeypatch.setattr(amenities, "fetch_amenities", boom)
    assert amenities.resolve(45.2, 9.2, 2)[0] == "failed"
    assert amenities.resolve(45.2, 9.2, 2) == ("failed", None, "down")
    assert amenities.peek(45.2, 9.2)[0] == "failed"
    assert len(calls) == 1


def test_concurrent_resolves_share_one_fetch(monkeypatch):
    import threading

    gate = threading.Event()
    calls = []

    def slow(lat, lon):
        calls.append(1)
        gate.wait(5)
        return AmenityScore()

    monkeypatch.setattr(amenities, "fetch_amenities", slow)
    assert amenities.resolve(45.3, 9.3, 0.05)[0] == "deferred"
    assert amenities.resolve(45.3, 9.3, 0.05)[0] == "deferred"
    gate.set()
    assert len(calls) == 1


def test_cache_defaults_are_24h():
    assert amenities.CACHE_TTL_S == 24 * 3600
    assert amenities._default_cache._ttl == 24 * 3600


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
