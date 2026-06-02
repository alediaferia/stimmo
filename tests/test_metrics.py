"""Tests for the per-route Prometheus metrics middleware."""

from __future__ import annotations

import pytest

from stimmo.web.metrics import LATENCY, REQUESTS, instrument

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(endpoint_name: str, status: int = 200):
    """Minimal ASGI app that sets scope['endpoint'] and responds with *status*."""

    class _FakeEndpoint:
        __name__ = endpoint_name

    async def _app(scope, receive, send):
        scope["endpoint"] = _FakeEndpoint()
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return _app


async def _call(app, path: str = "/", method: str = "GET") -> None:
    scope = {"type": "http", "path": path, "method": method}
    messages = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        messages.append(msg)

    await app(scope, receive, send)


def _counter_value(method: str, route: str, status: str) -> float:
    val = REQUESTS.labels(method=method, route=route, status=status)._value.get()
    return val


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_increments_on_request():
    app = instrument(_make_app("test_handler"))
    before = _counter_value("GET", "test_handler", "200")
    await _call(app, path="/some/path", method="GET")
    assert _counter_value("GET", "test_handler", "200") == before + 1


@pytest.mark.asyncio
async def test_counter_records_status_code():
    app = instrument(_make_app("error_handler", status=500))
    before = _counter_value("POST", "error_handler", "500")
    await _call(app, path="/bad", method="POST")
    assert _counter_value("POST", "error_handler", "500") == before + 1


@pytest.mark.asyncio
async def test_unmatched_route_label():
    """When scope has no 'endpoint' key, route label must be 'unmatched'."""

    async def _no_endpoint_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = instrument(_no_endpoint_app)
    before = _counter_value("GET", "unmatched", "404")
    await _call(app, path="/nowhere")
    assert _counter_value("GET", "unmatched", "404") == before + 1


@pytest.mark.asyncio
async def test_cardinality_stable_across_dynamic_paths():
    """Requests to the same endpoint with different path params map to one route label."""
    app = instrument(_make_app("form"))
    before = _counter_value("GET", "form", "200")
    await _call(app, path="/it/form")
    await _call(app, path="/en/form")
    await _call(app, path="/it/form?foo=bar")
    assert _counter_value("GET", "form", "200") == before + 3


@pytest.mark.asyncio
async def test_non_http_scope_passes_through():
    """Lifespan and websocket scopes must not be counted."""
    received = []

    async def _inner(scope, receive, send):
        received.append(scope["type"])

    app = instrument(_inner)
    lifespan_scope = {"type": "lifespan"}
    await app(lifespan_scope, None, None)
    assert received == ["lifespan"]


@pytest.mark.asyncio
async def test_latency_histogram_observed():
    """At least one sample should be recorded after a request."""
    app = instrument(_make_app("latency_test_handler"))

    hist = LATENCY.labels(method="GET", route="latency_test_handler")
    before = hist._sum.get()
    await _call(app, path="/latency")
    assert hist._sum.get() > before


def test_metrics_not_a_fastapi_route():
    """Confirm /metrics is not registered on the FastAPI app."""
    from stimmo.web.app import app as fastapi_app

    routes = [getattr(r, "path", None) for r in fastapi_app.routes]
    assert "/metrics" not in routes
