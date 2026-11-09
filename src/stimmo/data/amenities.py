"""OpenStreetMap Overpass — count amenities around the property.

Request-path design (the estimate handler must never wait on a degraded Overpass):

* ONE query at the outer radius (1000 m); the 500 m counts, the 500-1000 m ring and the
  map items are derived in Python by distance from the query point.
* One attempt per endpoint, no retry sleep, a short per-call timeout, and a mirror tried
  only while the overall budget has time left.
* ``resolve`` runs the fetch on a small bounded executor and waits at most the caller's
  budget; a fetch that outlives it keeps running in the background and warms the cache.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from importlib.metadata import version as _pkg_version

import requests

from stimmo.data import amenity_index
from stimmo.models import AmenityItem, AmenityScore
from stimmo.web import metrics

# OSM France's instance first: in Nov 2026 overpass-api.de, kumi.systems and private.coffee
# were all timing out while this one answered the Milan query in ~2 s.
OVERPASS = "https://overpass.openstreetmap.fr/api/interpreter"
OVERPASS_MIRROR = "https://overpass-api.de/api/interpreter"
OVERPASS_ENDPOINTS = (OVERPASS, OVERPASS_MIRROR)
UA = (
    f"stimmo/{_pkg_version('stimmo')} "
    "(https://stimmo.it; https://github.com/alediaferia/stimmo; Overpass amenity counter)"
)
_TIMEOUT = 4.0  # seconds per Overpass call on the request path
_MIN_CALL_BUDGET = 1.0  # don't start another endpoint with less than this left
_FETCH_BUDGET = 12.0  # overall cap for one fetch (all endpoints); background fetches too
_SERVER_TIMEOUT = 8  # Overpass-side [timeout:N]; the server gives up before we'd wait long
NEAR_M = 500  # "within 500 m" radius
FAR_M = 1000  # outer query radius; the 500-1000 m ring is derived from it

# Cache + de-duplication of in-flight / recently failed fetches.
CACHE_TTL_S = 24 * 3600
CACHE_MAXSIZE = 512
FAILURE_MEMO_TTL_S = 60.0  # replay a failure this long instead of re-hammering Overpass
_EXECUTOR_WORKERS = 4


class AmenityFetchError(Exception):
    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


# Caps to keep the +% adjustment bounded.
SCORE_CAPS = {
    "metro": 2.0,
    "tram": 1.0,
    "park": 1.0,
    "supermarket": 1.0,
    "school": 0.5,
    "pharmacy": 0.5,
}
# OMI zones already embed transit/amenity proximity; halved to avoid double-counting.
TOTAL_CAP_PCT = 2.5


def _post_overpass(query: str, *, deadline: float | None = None) -> dict:
    """POST a query to Overpass: a single attempt per endpoint, no sleeping.

    Endpoints are tried in order; the next one only while time remains before ``deadline``
    (a ``time.monotonic()`` value; default ``_FETCH_BUDGET`` from now).  Each call is
    capped at ``_TIMEOUT`` and at the time left.  ``AmenityFetchError.attempts`` is the
    number of endpoints actually tried.
    """
    if deadline is None:
        deadline = time.monotonic() + _FETCH_BUDGET
    last_err = "no time left"
    attempts = 0
    for url in OVERPASS_ENDPOINTS:
        remaining = deadline - time.monotonic()
        if attempts and remaining < _MIN_CALL_BUDGET:
            break
        timeout = max(0.1, min(_TIMEOUT, remaining))
        attempts += 1
        with metrics.time_upstream("overpass") as call:
            try:
                r = requests.post(
                    url, data={"data": query}, headers={"User-Agent": UA}, timeout=timeout
                )
                if r.status_code == 429:
                    call.outcome = "http_error"
                    last_err = "Overpass rate-limited (HTTP 429)"
                    continue
                if not r.ok:
                    call.outcome = "http_error"
                    last_err = f"HTTP {r.status_code}"
                    continue
                data = r.json()
            except requests.RequestException as e:
                call.outcome = "timeout" if isinstance(e, requests.Timeout) else "error"
                last_err = str(e) or type(e).__name__
                continue
            except ValueError as e:  # body was not JSON
                call.outcome = "error"
                last_err = f"invalid Overpass response: {e}"
                continue
            remark = data.get("remark", "")
            if ("runtime error" in remark or "timeout" in remark) and not data.get("elements"):
                call.outcome = "error"
                last_err = f"Overpass soft-failure: {remark}"
                continue
        return data

    raise AmenityFetchError(last_err, attempts=attempts)


def _query(lat: float, lon: float, radius: int = FAR_M, *, deadline: float | None = None) -> dict:
    around = f"around:{radius},{lat},{lon}"
    q = f"""
[out:json][timeout:{_SERVER_TIMEOUT}];
(
  node["railway"="station"]["station"="subway"]({around});
  node["station"="subway"]({around});
  node["railway"="tram_stop"]({around});
  way["leisure"="park"]({around});
  node["shop"="supermarket"]({around});
  way["shop"="supermarket"]({around});
  node["amenity"="school"]({around});
  way["amenity"="school"]({around});
  node["amenity"="pharmacy"]({around});
);
out tags geom;
"""
    return _post_overpass(q, deadline=deadline)


def _classify(el: dict) -> str | None:
    t = el.get("tags", {})
    if t.get("station") == "subway" or (
        t.get("railway") == "station" and t.get("station") == "subway"
    ):
        return "metro"
    if t.get("railway") == "tram_stop":
        return "tram"
    if t.get("leisure") == "park":
        # Only count "large" parks; geometry-based area is overkill — use a node count proxy.
        coords = el.get("geometry") or []
        if len(coords) > 20:
            return "park"
        return None
    if t.get("shop") == "supermarket":
        return "supermarket"
    if t.get("amenity") == "school":
        return "school"
    if t.get("amenity") == "pharmacy":
        return "pharmacy"
    return None


def _centroid(geometry: list[dict]) -> tuple[float, float]:
    lats = [p["lat"] for p in geometry if "lat" in p]
    lons = [p["lon"] for p in geometry if "lon" in p]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_in_polygon(lat: float, lon: float, geom: list[dict]) -> bool:
    inside = False
    n = len(geom)
    for i in range(n):
        a, b = geom[i], geom[(i + 1) % n]
        if (a["lon"] > lon) != (b["lon"] > lon):
            t = (lon - a["lon"]) / (b["lon"] - a["lon"])
            if lat < a["lat"] + t * (b["lat"] - a["lat"]):
                inside = not inside
    return inside


def _distance_m(el: dict, lat: float, lon: float) -> float | None:
    """Distance from the query point to an element, mirroring Overpass ``around:``.

    A node is its own point.  A way matches ``around`` when ANY part of it is within the
    radius, so its distance is 0 when the point lies inside it, else the nearest vertex
    (the centroid is also considered, so a way with sparse vertices is not pushed away).
    None when the element carries no usable position.
    """
    if "lat" in el and "lon" in el:
        return _haversine_m(lat, lon, el["lat"], el["lon"])
    geom = [p for p in (el.get("geometry") or []) if "lat" in p and "lon" in p]
    if not geom:
        return None
    if len(geom) >= 3 and _point_in_polygon(lat, lon, geom):
        return 0.0
    best = min(_haversine_m(lat, lon, p["lat"], p["lon"]) for p in geom)
    try:
        clat, clon = _centroid(geom)
        best = min(best, _haversine_m(lat, lon, clat, clon))
    except ZeroDivisionError:
        pass
    return best


def _count_elements(data: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for el in data.get("elements", []):
        kind = _classify(el)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def _within(data: dict, lat: float, lon: float, radius_m: float) -> dict:
    """The subset of Overpass ``data`` whose elements lie within ``radius_m`` of the point."""
    kept = []
    for el in data.get("elements", []):
        d = _distance_m(el, lat, lon)
        if d is not None and d <= radius_m:
            kept.append(el)
    return {**data, "elements": kept}


def _extract_items(data: dict) -> list[AmenityItem]:
    items: list[AmenityItem] = []
    for el in data.get("elements", []):
        kind = _classify(el)
        if not kind:
            continue
        name: str | None = el.get("tags", {}).get("name")
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif el.get("geometry"):
            try:
                lat, lon = _centroid(el["geometry"])
            except (ZeroDivisionError, KeyError):
                continue
        else:
            continue
        items.append(AmenityItem(kind=kind, name=name, lat=lat, lon=lon))  # type: ignore[arg-type]
    # Metro/tram first (most identity-bearing), then parks, then rest; cap at 12
    order = ["metro", "tram", "park", "supermarket", "school", "pharmacy"]
    items.sort(key=lambda x: order.index(x.kind) if x.kind in order else 99)
    return items[:12]


def _score_for_counts(kind: str, count: int, weight: float = 1.0) -> float:
    if count <= 0:
        return 0.0
    cap = SCORE_CAPS[kind]
    if kind in ("metro", "tram", "pharmacy", "park"):
        return cap * weight
    else:  # supermarket, school — diminishing returns up to 3
        return cap * weight if count >= 3 else cap * weight * (count / 3)


def _score_from_data(data_1000: dict, lat: float, lon: float) -> AmenityScore:
    """Derive the 500 m counts, the 500-1000 m ring and the score from one 1000 m result."""
    data_500 = _within(data_1000, lat, lon, NEAR_M)
    counts_500 = _count_elements(data_500)
    counts_1000 = _count_elements(data_1000)
    # Ring = amenities exclusive to the 500–1000 m band
    counts_ring = {k: max(0, counts_1000.get(k, 0) - counts_500.get(k, 0)) for k in SCORE_CAPS}

    raw_score = 0.0
    for kind in SCORE_CAPS:
        raw_score += _score_for_counts(kind, counts_500.get(kind, 0), weight=1.0)
        raw_score += _score_for_counts(kind, counts_ring.get(kind, 0), weight=0.5)

    score = min(raw_score, TOTAL_CAP_PCT)
    return AmenityScore(
        metro_within_500m=counts_500.get("metro", 0),
        tram_within_500m=counts_500.get("tram", 0),
        parks_large_within_500m=counts_500.get("park", 0),
        supermarkets_within_500m=counts_500.get("supermarket", 0),
        schools_within_500m=counts_500.get("school", 0),
        pharmacies_within_500m=counts_500.get("pharmacy", 0),
        metro_500_1000m=counts_ring.get("metro", 0),
        tram_500_1000m=counts_ring.get("tram", 0),
        parks_large_500_1000m=counts_ring.get("park", 0),
        supermarkets_500_1000m=counts_ring.get("supermarket", 0),
        schools_500_1000m=counts_ring.get("school", 0),
        pharmacies_500_1000m=counts_ring.get("pharmacy", 0),
        score_pct=round(score, 2),
        items_within_500m=_extract_items(data_500),
    )


def _fetch_amenities_uncached(lat: float, lon: float) -> AmenityScore:
    """Fetch amenity data from Overpass (one query) without any caching.

    With ``STIMMO_AMENITIES_BUNDLED`` on, the bundled OSM extract answers instead (no network).
    """
    if amenity_index.bundled_enabled():
        try:
            data = amenity_index.elements_within(lat, lon, FAR_M)
        except OSError as e:
            raise AmenityFetchError(f"bundled amenity asset unavailable: {e}", attempts=0) from e
        return _score_from_data(data, lat, lon)
    deadline = time.monotonic() + _FETCH_BUDGET
    return _score_from_data(_query(lat, lon, FAR_M, deadline=deadline), lat, lon)


# ---------------------------------------------------------------------------
# TTL+LRU cache for fetch_amenities (D9, §5.8)
# ---------------------------------------------------------------------------


class _AmenityCache:
    """Process-local TTL+LRU cache for AmenityScore results.

    Keyed by quantized (lat, lon) coordinates — ``round(_, 3)`` gives a
    ~111 m grid that coalesces near-identical opens (D9).

    Design mirrors ``InMemoryCache`` in ``stimmo/mcp/cache.py``:
    injectable clock for deterministic tests; LRU eviction via OrderedDict.

    Parameters
    ----------
    ttl_s:
        Time-to-live in seconds per entry.  Default 24 h.
    maxsize:
        Maximum number of cached entries.  Least-recently-used entry is
        evicted when the limit is reached.  Default 512.
    clock:
        Callable returning the current monotonic time in seconds.
        Injectable for tests; default ``time.monotonic``.
    """

    def __init__(
        self,
        *,
        ttl_s: float = CACHE_TTL_S,
        maxsize: int = CACHE_MAXSIZE,
        clock=time.monotonic,
    ) -> None:
        self._ttl = ttl_s
        self._maxsize = maxsize
        self._clock = clock
        # Maps key → (stored_at, AmenityScore); OrderedDict maintains LRU order.
        self._store: OrderedDict[tuple[float, float], tuple[float, AmenityScore]] = OrderedDict()
        # Handlers and background fetches touch the cache from different threads.
        self._lock = threading.Lock()

    def get(self, key: tuple[float, float]) -> AmenityScore | None:
        """Return cached AmenityScore for *key*, or None if absent/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            stored_at, score = entry
            if self._clock() - stored_at > self._ttl:
                # Expired — remove and return None.
                del self._store[key]
                return None
            # Bump to most-recently-used position.
            self._store.move_to_end(key)
            return score

    def put(self, key: tuple[float, float], value: AmenityScore) -> None:
        """Store *value* under *key*, evicting the LRU entry if at capacity."""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (self._clock(), value)
            if len(self._store) > self._maxsize:
                self._store.popitem(last=False)  # evict oldest (LRU)


_default_cache: _AmenityCache = _AmenityCache()


def fetch_amenities(
    lat: float, lon: float, *, _cache: _AmenityCache = _default_cache
) -> AmenityScore:
    """Return amenity data for the given coordinates, using a TTL+LRU cache.

    The cache key is the quantized coordinate pair ``(round(lat, 3), round(lon, 3))``,
    giving a ~111 m grid that coalesces near-identical opens (D9, §5.8).  The
    underlying Overpass queries are only fired on a cache miss or after TTL expiry.

    The ``_cache`` parameter is injectable for tests; production code always uses
    the module-level ``_default_cache`` singleton.
    """
    key = (round(lat, 3), round(lon, 3))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    score = _fetch_amenities_uncached(lat, lon)
    _cache.put(key, score)
    return score


# ---------------------------------------------------------------------------
# Budgeted resolution for the request path
# ---------------------------------------------------------------------------

# Bounded on purpose: a fetch that outlives its request's budget keeps running here (it
# warms the cache for the client-side follow-up), but a degraded Overpass can never pile up
# more than this many threads.  Extra work queues behind them.
_executor = ThreadPoolExecutor(max_workers=_EXECUTOR_WORKERS, thread_name_prefix="amenity")
_inflight: dict[tuple[float, float], Future] = {}
_failures: dict[tuple[float, float], tuple[float, str]] = {}
_state_lock = threading.Lock()
_now: Callable[[], float] = time.monotonic  # injectable for tests


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 3), round(lon, 3))


def _run_background(key: tuple[float, float], lat: float, lon: float) -> AmenityScore:
    """Executor body: fetch (cached), record a failure for a short while, clear in-flight."""
    try:
        score = fetch_amenities(lat, lon)
        _default_cache.put(key, score)  # also when fetch_amenities is stubbed
        return score
    except Exception as e:
        with _state_lock:
            _failures[key] = (_now(), str(e) or type(e).__name__)
        raise
    finally:
        with _state_lock:
            _inflight.pop(key, None)


def _recent_failure(key: tuple[float, float]) -> str | None:
    with _state_lock:
        entry = _failures.get(key)
        if entry is None:
            return None
        if _now() - entry[0] > FAILURE_MEMO_TTL_S:
            del _failures[key]
            return None
        return entry[1]


def _start(key: tuple[float, float], lat: float, lon: float) -> Future:
    """The in-flight future for ``key``, submitting one if there is none."""
    with _state_lock:
        fut = _inflight.get(key)
        if fut is None:
            _failures.pop(key, None)
            fut = _executor.submit(_run_background, key, lat, lon)
            _inflight[key] = fut
        return fut


def resolve(lat: float, lon: float, budget_s: float) -> tuple[str, AmenityScore | None, str | None]:
    """Resolve amenities within ``budget_s`` seconds.

    Returns ``(status, score, error)`` where status is ``cached`` | ``ok`` | ``failed`` |
    ``deferred``.  ``deferred`` means the budget expired with the fetch still running in
    the background; ``peek`` (or a later ``resolve``) picks the result up from the cache.
    """
    key = _key(lat, lon)
    hit = _default_cache.get(key)
    if hit is not None:
        return "cached", hit, None
    err = _recent_failure(key)
    if err is not None:
        return "failed", None, err
    fut = _start(key, lat, lon)
    try:
        return "ok", fut.result(timeout=budget_s), None
    except FutureTimeout:
        return "deferred", None, None
    except Exception as e:
        return "failed", None, str(e) or type(e).__name__


def peek(lat: float, lon: float) -> tuple[str, AmenityScore | None, str | None]:
    """Non-blocking state for polling: ``ok`` | ``pending`` | ``failed``.

    Starts a background fetch when nothing is cached, running or recently failed, so a
    page that was served ``deferred`` always converges.
    """
    key = _key(lat, lon)
    hit = _default_cache.get(key)
    if hit is not None:
        return "ok", hit, None
    err = _recent_failure(key)
    if err is not None:
        return "failed", None, err
    fut = _start(key, lat, lon)
    if fut.done():  # finished between the checks above
        hit = _default_cache.get(key)
        if hit is not None:
            return "ok", hit, None
        err = _recent_failure(key)
        if err is not None:
            return "failed", None, err
    return "pending", None, None
