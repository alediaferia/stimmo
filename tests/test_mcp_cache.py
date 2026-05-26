from __future__ import annotations

from stimmo.mcp.cache import InMemoryCache


def _make_clock(initial: float = 0.0):
    t = [initial]

    def clock() -> float:
        return t[0]

    def advance(seconds: float) -> None:
        t[0] += seconds

    return clock, advance


def test_ttl_expiry():
    clock, advance = _make_clock()
    cache = InMemoryCache(clock=clock)
    cache.set("k", "v", ttl=10)
    assert cache.get("k") == "v"
    advance(9)
    assert cache.get("k") == "v"
    advance(2)  # total 11 s — past TTL
    assert cache.get("k") is None


def test_forever_ttl():
    clock, advance = _make_clock()
    cache = InMemoryCache(clock=clock)
    cache.set("k", "v", ttl=None)
    advance(1_000_000)
    assert cache.get("k") == "v"


def test_lru_eviction():
    clock, _ = _make_clock()
    cache = InMemoryCache(max_entries=3, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    # access "a" to make it recently used
    cache.get("a")
    # insert "d" — should evict "b" (least recently used)
    cache.set("d", 4)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.get("d") == 4


def test_overwrite_updates_ttl():
    clock, advance = _make_clock()
    cache = InMemoryCache(clock=clock)
    cache.set("k", "old", ttl=5)
    advance(3)
    cache.set("k", "new", ttl=10)
    advance(8)  # 11 s from original set, 8 s from re-set — still within new TTL
    assert cache.get("k") == "new"
    advance(3)  # 11 s from re-set — past new TTL
    assert cache.get("k") is None


def test_coordinate_rounding_cache_key_behavior():
    """Overpass cache: keys rounded to 4 dp — points < ~11 m apart share a slot.
    Nominatim cache: keys are address strings — never collide on proximity."""
    clock, _ = _make_clock()
    cache = InMemoryCache(clock=clock)

    # Two points whose lat/lon differ only beyond 4 dp → same key
    # 45.46421 and 45.46424 both round to 45.4642; 9.19001 and 9.19004 both round to 9.1900
    lat1, lon1 = 45.46421, 9.19001
    lat2, lon2 = 45.46424, 9.19004

    key1 = f"{lat1:.4f},{lon1:.4f}"
    key2 = f"{lat2:.4f},{lon2:.4f}"
    assert key1 == key2  # same rounded key

    cache.set(key1, "result_a")
    assert cache.get(key2) == "result_a"

    # Two addresses 1 m apart — different string keys, no collision
    addr1 = "Via Roma 1, Milano"
    addr2 = "Via Roma 2, Milano"
    cache.set(addr1.lower().strip(), "geo_a")
    assert cache.get(addr2.lower().strip()) is None
