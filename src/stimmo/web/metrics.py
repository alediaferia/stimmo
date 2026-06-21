from __future__ import annotations

import time
from typing import TYPE_CHECKING

import prometheus_client

if TYPE_CHECKING:
    from collections.abc import Callable

REQUESTS: prometheus_client.Counter = prometheus_client.Counter(
    "stimmo_http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status"],
)

LATENCY: prometheus_client.Histogram = prometheus_client.Histogram(
    "stimmo_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "route"],
)

# Share-link adoption + token-health. `outcome` is needed because invalid/expired
# tokens are served as normal 200/404 responses and would otherwise be invisible
# in the HTTP counters. event=open (share page) | og_render (social card image);
# outcome=ok | invalid (bad/forged token) | error (valid token, recompute failed).
SHARE_EVENTS: prometheus_client.Counter = prometheus_client.Counter(
    "stimmo_share_events_total",
    "Share-link open / OG-render outcomes",
    ["event", "outcome"],
)

# Resolve-path breakdown: distinguishes store hits, legacy-token fallbacks, and
# complete misses.  Incremented in handlers (not _dispatch) per the observability
# invariant.  path=store | legacy | miss.
SHARE_RESOLVE: prometheus_client.Counter = prometheus_client.Counter(
    "stimmo_share_resolve_total",
    "Share-link resolve path (store id / legacy token / miss)",
    ["path"],
)


def instrument(app_callable: Callable) -> Callable:
    """Wrap an ASGI callable to record per-route request count and latency."""

    async def _wrapped(scope, receive, send):
        if scope.get("type") != "http":
            await app_callable(scope, receive, send)
            return

        start = time.perf_counter()
        method = scope.get("method", "")
        status_code: list[int] = [500]

        async def _send(message):
            if message["type"] == "http.response.start":
                status_code[0] = message.get("status", 500)
            await send(message)

        try:
            await app_callable(scope, receive, _send)
        finally:
            ep = scope.get("endpoint")
            route = getattr(ep, "__name__", None) or "unmatched"
            elapsed = time.perf_counter() - start
            status = str(status_code[0])
            REQUESTS.labels(method=method, route=route, status=status).inc()
            LATENCY.labels(method=method, route=route).observe(elapsed)

    return _wrapped


def start_metrics_server(port: int) -> None:
    prometheus_client.start_http_server(port, addr="0.0.0.0")
