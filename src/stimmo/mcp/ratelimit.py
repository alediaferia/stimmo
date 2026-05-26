from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_EXPENSIVE_TOOLS = frozenset({"estimate_property", "amenity_score", "parse_immobiliare_listing"})
_CHEAP_CAPACITY = 60
_EXPENSIVE_CAPACITY = 10
_WINDOW = 60.0  # seconds


class _Bucket:
    def __init__(self, capacity: int, window: float, clock: Callable[[], float]) -> None:
        self._capacity = capacity
        self._window = window
        self._clock = clock
        self._tokens = capacity
        self._last_refill = clock()

    def consume(self) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed >= self._window:
            self._tokens = self._capacity
            self._last_refill = now
        if self._tokens > 0:
            self._tokens -= 1
            return True, 0.0
        retry_after = self._window - (now - self._last_refill)
        return False, max(0.0, retry_after)


def _is_expensive_call(body: bytes) -> bool:
    try:
        data = json.loads(body)
        items = data if isinstance(data, list) else [data]
        return any(
            isinstance(item, dict)
            and item.get("method") == "tools/call"
            and item.get("params", {}).get("name", "") in _EXPENSIVE_TOOLS
            for item in items
        )
    except (json.JSONDecodeError, AttributeError):
        return False


def _extract_ip(scope: Scope) -> str:
    headers = {k.lower(): v for k, v in scope.get("headers", [])}
    cf_ip = headers.get(b"cf-connecting-ip")
    if cf_ip:
        return cf_ip.decode()
    logger.warning("CF-Connecting-IP header missing, falling back to client host")
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._app = app
        self._clock = clock
        self._cheap: dict[str, _Bucket] = {}
        self._expensive: dict[str, _Bucket] = {}

    def _bucket(self, ip: str, expensive: bool) -> _Bucket:
        store = self._expensive if expensive else self._cheap
        if ip not in store:
            capacity = _EXPENSIVE_CAPACITY if expensive else _CHEAP_CAPACITY
            store[ip] = _Bucket(capacity, _WINDOW, self._clock)
        return store[ip]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        ip = _extract_ip(scope)

        if method == "POST":
            # Buffer body to determine tier.
            chunks: list[bytes] = []
            while True:
                msg = await receive()
                if msg["type"] == "http.request":
                    chunks.append(msg.get("body", b""))
                    if not msg.get("more_body", False):
                        break
            full_body = b"".join(chunks)
            expensive = _is_expensive_call(full_body)

            # Replay body for the downstream app.
            replayed = False

            async def replay_receive() -> dict:
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": full_body, "more_body": False}
                return await receive()

            downstream_receive = replay_receive
        else:
            expensive = False
            downstream_receive = receive

        allowed, retry_after = self._bucket(ip, expensive).consume()
        if not allowed:
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32000,
                        "message": "rate_limited",
                        "data": {
                            "code": "rate_limited",
                            "message": "Rate limit exceeded. Try again later.",
                        },
                    },
                    "id": None,
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(int(retry_after) + 1).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return

        await self._app(scope, downstream_receive, send)
