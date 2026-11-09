from __future__ import annotations

import time
from contextlib import contextmanager
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


# Interest in a not-yet-available feature, counted anonymously. event=view (the button was
# rendered on a result page) | click (the same-origin POST was received); lang is the URL
# language slug (it | en) — the only label, so cardinality is fixed at 2 x 2. Incremented in
# the handlers, not _dispatch, per the observability invariant: a click is a 204 with no body
# and could not be told apart from any other POST in the HTTP counters.
DOSSIER_INTEREST: prometheus_client.Counter = prometheus_client.Counter(
    "stimmo_dossier_interest_total",
    "Anonymous view / click counts for the dossier interest button",
    ["event", "lang"],
)

# Outbound calls to the two live upstreams. upstream=nominatim | overpass; outcome=ok |
# timeout | http_error | error. Recorded around the `requests` calls in data/geocode.py and
# data/amenities.py (via `time_upstream`), never by URL, so cardinality is fixed at 2 x 4.
UPSTREAM_DURATION: prometheus_client.Histogram = prometheus_client.Histogram(
    "stimmo_upstream_duration_seconds",
    "Duration of outbound Nominatim / Overpass calls",
    ["upstream", "outcome"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 45),
)

# What the estimate handler did about amenities. status=ok (fetched inside the budget) |
# cached (served from the amenity cache) | deferred (budget expired, page rendered without
# amenities and finished client-side) | failed (upstream error, or a recent failure replayed).
ESTIMATE_AMENITY: prometheus_client.Counter = prometheus_client.Counter(
    "stimmo_estimate_amenity_total",
    "Amenity resolution outcome of POST /{lang}/estimate",
    ["status"],
)


class _UpstreamCall:
    """Mutable outcome holder yielded by `time_upstream`."""

    __slots__ = ("outcome",)

    def __init__(self) -> None:
        self.outcome = "ok"


@contextmanager
def time_upstream(upstream: str):
    """Time one outbound call. Set ``call.outcome = "http_error"`` for a non-2xx response that
    does not raise; an exception is classified timeout (requests.Timeout) or error."""
    import requests  # local: keeps this module importable without the HTTP stack

    call = _UpstreamCall()
    start = time.perf_counter()
    try:
        yield call
    except requests.HTTPError:
        call.outcome = "http_error"
        raise
    except requests.Timeout:
        call.outcome = "timeout"
        raise
    except BaseException:
        call.outcome = "error"
        raise
    finally:
        UPSTREAM_DURATION.labels(upstream=upstream, outcome=call.outcome).observe(
            time.perf_counter() - start
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
