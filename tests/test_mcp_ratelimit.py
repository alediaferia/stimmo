from __future__ import annotations

import json

from starlette.testclient import TestClient

from stimmo.mcp.ratelimit import RateLimitMiddleware


def _make_echo_app():
    async def echo(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return echo


def _make_clock(initial: float = 0.0):
    t = [initial]

    def clock() -> float:
        return t[0]

    def advance(seconds: float) -> None:
        t[0] += seconds

    return clock, advance


def _cheap_body() -> dict:
    return {"jsonrpc": "2.0", "method": "resources/list", "id": 1}


def _expensive_body(tool: str = "estimate_property") -> dict:
    return {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool}, "id": 1}


def test_cheap_tier_allows_60_rejects_61st():
    clock, _ = _make_clock()
    mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
    client = TestClient(mw, raise_server_exceptions=False)
    headers = {"cf-connecting-ip": "1.2.3.4"}

    for _ in range(60):
        r = client.post("/", json=_cheap_body(), headers=headers)
        assert r.status_code == 200

    r = client.post("/", json=_cheap_body(), headers=headers)
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["data"]["code"] == "rate_limited"
    assert "retry-after" in r.headers


def test_expensive_tier_allows_10_rejects_11th():
    clock, _ = _make_clock()
    mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
    client = TestClient(mw, raise_server_exceptions=False)
    headers = {"cf-connecting-ip": "10.0.0.1"}

    for _ in range(10):
        r = client.post("/", json=_expensive_body(), headers=headers)
        assert r.status_code == 200

    r = client.post("/", json=_expensive_body(), headers=headers)
    assert r.status_code == 429


def test_two_ips_have_separate_buckets():
    clock, _ = _make_clock()
    mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
    client = TestClient(mw, raise_server_exceptions=False)

    for _ in range(60):
        client.post("/", json=_cheap_body(), headers={"cf-connecting-ip": "1.1.1.1"})

    # IP 1 is exhausted but IP 2 still has capacity
    r = client.post("/", json=_cheap_body(), headers={"cf-connecting-ip": "1.1.1.1"})
    assert r.status_code == 429

    r = client.post("/", json=_cheap_body(), headers={"cf-connecting-ip": "2.2.2.2"})
    assert r.status_code == 200


def test_cf_connecting_ip_takes_priority_over_x_forwarded_for():
    clock, _ = _make_clock()
    mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
    client = TestClient(mw, raise_server_exceptions=False)

    # Exhaust the bucket for CF IP "3.3.3.3"
    for _ in range(60):
        client.post(
            "/",
            json=_cheap_body(),
            headers={"cf-connecting-ip": "3.3.3.3", "x-forwarded-for": "9.9.9.9"},
        )

    # Same CF IP — still exhausted regardless of X-Forwarded-For
    r = client.post(
        "/",
        json=_cheap_body(),
        headers={"cf-connecting-ip": "3.3.3.3", "x-forwarded-for": "9.9.9.9"},
    )
    assert r.status_code == 429

    # Different CF IP with same X-Forwarded-For — fresh bucket
    r = client.post(
        "/",
        json=_cheap_body(),
        headers={"cf-connecting-ip": "4.4.4.4", "x-forwarded-for": "9.9.9.9"},
    )
    assert r.status_code == 200


def test_missing_cf_header_falls_back_to_client_host(caplog):
    import logging

    clock, _ = _make_clock()
    mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
    client = TestClient(mw, raise_server_exceptions=False)

    with caplog.at_level(logging.WARNING, logger="stimmo.mcp.ratelimit"):
        r = client.post("/", json=_cheap_body())  # no CF header

    assert r.status_code == 200
    assert any("CF-Connecting-IP" in m for m in caplog.messages)


def test_token_refill_after_window():
    clock, advance = _make_clock()
    mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
    client = TestClient(mw, raise_server_exceptions=False)
    headers = {"cf-connecting-ip": "5.5.5.5"}

    # Exhaust cheap tier
    for _ in range(60):
        client.post("/", json=_cheap_body(), headers=headers)

    r = client.post("/", json=_cheap_body(), headers=headers)
    assert r.status_code == 429

    # Advance past the 60-second window — tokens refill
    advance(61)
    r = client.post("/", json=_cheap_body(), headers=headers)
    assert r.status_code == 200


def test_retry_after_header_value():
    clock, advance = _make_clock()
    mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
    client = TestClient(mw, raise_server_exceptions=False)
    headers = {"cf-connecting-ip": "6.6.6.6"}

    # Exhaust the bucket (bucket _last_refill starts at t=0)
    for _ in range(60):
        client.post("/", json=_cheap_body(), headers=headers)

    # Advance 30 s into the 60 s window
    advance(30)

    r = client.post("/", json=_cheap_body(), headers=headers)
    assert r.status_code == 429
    retry_after = int(r.headers["retry-after"])
    # ~30 s remaining (60 - 30 = 30, +1 ceiling = 31)
    assert 28 <= retry_after <= 32


def test_all_expensive_tools_use_expensive_bucket():
    expensive_tools = ["estimate_property", "amenity_score", "parse_immobiliare_listing"]
    for tool_name in expensive_tools:
        clock, _ = _make_clock()
        mw = RateLimitMiddleware(_make_echo_app(), clock=clock)
        client = TestClient(mw, raise_server_exceptions=False)
        headers = {"cf-connecting-ip": "7.7.7.7"}

        for _ in range(10):
            r = client.post("/", json=_expensive_body(tool_name), headers=headers)
            assert r.status_code == 200, f"{tool_name}: expected 200 on call {_+1}"

        r = client.post("/", json=_expensive_body(tool_name), headers=headers)
        assert r.status_code == 429, f"{tool_name}: expected 429 on 11th call"
