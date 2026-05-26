"""End-to-end integration test: estimate_property through the mounted /mcp endpoint.

OMI lookup and zone lookup hit real bundled data (deterministic, fast).
Only Nominatim and Overpass are monkeypatched.
"""

from __future__ import annotations

import json

import httpx
import pytest

from stimmo.data import amenities, geocode
from stimmo.data import omi as omi_mod
from stimmo.models import AmenityScore
from tests.conftest import parse_mcp_response


@pytest.fixture()
async def http_client(_mcp_lifespan):
    """AsyncClient with IP 127.0.0.2 (separate rate-limit bucket from protocol tests)."""
    from stimmo.web.app import application

    yield httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://localhost:8000",
        headers={
            "cf-connecting-ip": "127.0.0.2",
            "accept": "application/json, text/event-stream",
        },
    )


async def _initialize(client: httpx.AsyncClient) -> str | None:
    resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-e2e", "version": "0.1"},
            },
            "id": 0,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.headers.get("mcp-session-id")


async def test_estimate_property_end_to_end(monkeypatch, http_client):
    """Happy path: real zone + OMI data, stubbed geocode + amenities."""
    # Piazza Duomo falls in a well-covered central zone.
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    async with http_client as client:
        session_id = await _initialize(client)

        headers = {}
        if session_id:
            headers["mcp-session-id"] = session_id

        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "estimate_property",
                    "arguments": {
                        "address": "Piazza Duomo, Milano",
                        "surface_m2": 80.0,
                        "property_type": "Abitazioni civili",
                        "fine_condition": "abitabile",
                        "floor": 2,
                        "total_floors": 5,
                        "has_lift": True,
                        "asking_price_eur": 650000.0,
                        "construction_era": "postwar_boom",
                    },
                },
                "id": 1,
            },
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    data = parse_mcp_response(resp)
    assert "result" in data, data
    result = data["result"]
    assert result.get("isError") is not True, result

    text = result["content"][0]["text"]
    estimate = json.loads(text)

    assert estimate["verdict"] in ("under", "fair", "over")
    assert len(estimate["breakdown"]) > 0

    # OMI semester must come from the bundled data
    assert estimate["omi_quote"]["semester"] == omi_mod.semester()
