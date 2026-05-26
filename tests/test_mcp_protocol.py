"""Protocol-level MCP tests via httpx.ASGITransport.

These tests drive the mounted /mcp endpoint with raw JSON-RPC calls to catch
schema-generation mistakes and protocol-compliance issues that pure Python
unit tests would miss.
"""

from __future__ import annotations

import json

import httpx

from stimmo.data import amenities, geocode, zones
from stimmo.models import AmenityScore
from tests.conftest import parse_mcp_response


async def _initialize(client: httpx.AsyncClient) -> str | None:
    """Send MCP initialize and return the session ID (may be None for stateless servers)."""
    resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.1"},
            },
            "id": 0,
        },
    )
    assert resp.status_code == 200, resp.text
    session_id = resp.headers.get("mcp-session-id")
    return session_id


async def _rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict | None = None,
    *,
    session_id: str | None = None,
    rpc_id: int = 1,
) -> dict:
    headers = {}
    if session_id:
        headers["mcp-session-id"] = session_id
    resp = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": method, "params": params or {}, "id": rpc_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return parse_mcp_response(resp)


async def test_tools_list(monkeypatch, http_client):
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    async with http_client as client:
        session_id = await _initialize(client)
        data = await _rpc(client, "tools/list", session_id=session_id)

    assert "result" in data, data
    tool_names = {t["name"] for t in data["result"]["tools"]}
    expected = {
        "estimate_property",
        "lookup_omi_quote",
        "geocode_milan_address",
        "omi_zone_for_point",
        "amenity_score",
        "omi_history",
    }
    assert expected <= tool_names


async def test_resources_list(http_client):
    async with http_client as client:
        session_id = await _initialize(client)
        data = await _rpc(client, "resources/list", session_id=session_id)

    assert "result" in data, data
    uris = {r["uri"] for r in data["result"]["resources"]}
    assert "stimmo://semester" in uris
    assert "stimmo://vocab/property-type" in uris
    assert "stimmo://omi-zones" in uris


async def test_resources_read_property_type(http_client):
    async with http_client as client:
        session_id = await _initialize(client)
        data = await _rpc(
            client,
            "resources/read",
            {"uri": "stimmo://vocab/property-type"},
            session_id=session_id,
        )

    assert "result" in data, data
    contents = data["result"]["contents"]
    text = contents[0]["text"]
    vocab = json.loads(text)
    assert any(item["value"] == "Abitazioni civili" for item in vocab)
    # Italian hints are present and non-empty for every item
    assert all(item.get("hint") for item in vocab)


async def test_prompts_list(http_client):
    async with http_client as client:
        session_id = await _initialize(client)
        data = await _rpc(client, "prompts/list", session_id=session_id)

    assert "result" in data, data
    names = {p["name"] for p in data["result"]["prompts"]}
    assert "appraise_listing" in names


async def test_prompts_get(http_client):
    async with http_client as client:
        session_id = await _initialize(client)
        data = await _rpc(
            client,
            "prompts/get",
            {"name": "appraise_listing", "arguments": {"listing": "Via Roma 1, Milano — 80 m²"}},
            session_id=session_id,
        )

    assert "result" in data, data
    messages = data["result"]["messages"]
    assert len(messages) > 0
    content = (
        messages[0]["content"]["text"]
        if isinstance(messages[0]["content"], dict)
        else str(messages[0]["content"])
    )
    assert "estimate_property" in content


async def test_estimate_property_tool_call(monkeypatch, http_client):
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())

    args = {
        "address": "Piazza Duomo, Milano",
        "surface_m2": 80.0,
        "property_type": "Abitazioni civili",
        "fine_condition": "abitabile",
        "floor": 2,
        "total_floors": 5,
        "has_lift": True,
        "asking_price_eur": 650000.0,
        "construction_era": "postwar_boom",
    }

    async with http_client as client:
        session_id = await _initialize(client)
        data = await _rpc(
            client,
            "tools/call",
            {"name": "estimate_property", "arguments": args},
            session_id=session_id,
        )

    assert "result" in data, data
    result = data["result"]
    assert result.get("isError") is not True, result
    text = result["content"][0]["text"]
    estimate = json.loads(text)
    assert estimate["verdict"] in ("under", "fair", "over")
    assert len(estimate["breakdown"]) > 0


async def test_outside_milan_error(monkeypatch, http_client):
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(zones, "zone_for_point", lambda lat, lon: None)

    args = {
        "address": "Outside Milano",
        "surface_m2": 60.0,
        "property_type": "Abitazioni civili",
        "fine_condition": "abitabile",
        "floor": 1,
        "total_floors": 3,
        "has_lift": False,
        "asking_price_eur": 200000.0,
        "construction_era": "postwar_boom",
    }

    async with http_client as client:
        session_id = await _initialize(client)
        data = await _rpc(
            client,
            "tools/call",
            {"name": "estimate_property", "arguments": args},
            session_id=session_id,
        )

    assert "result" in data, data
    result = data["result"]
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    # MCP wraps ToolError as "Error executing tool <name>: <json>"
    json_start = text.find("{")
    assert json_start != -1, f"No JSON in error text: {text!r}"
    error = json.loads(text[json_start:])
    assert error["code"] == "outside_milan"
