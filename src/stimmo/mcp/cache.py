from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...


class InMemoryCache:
    """LRU-eviction + TTL cache with injectable clock for deterministic testing."""

    def __init__(
        self,
        max_entries: int | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_entries
        self._clock = clock
        # value, expires_at (None = forever)
        self._store: OrderedDict[str, tuple[Any, float | None]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires_at = self._clock() + ttl if ttl is not None else None
        if key in self._store:
            del self._store[key]
        self._store[key] = (value, expires_at)
        if self._max is not None and len(self._store) > self._max:
            self._store.popitem(last=False)
