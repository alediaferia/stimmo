"""MCP surface of the purchase-cost calculator: the `estimate_purchase_costs` tool, its vocab
resources and its rate-limit tier. The tool is a thin wrapper over costs/calculator.py."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from stimmo.costs import rates as costs_rates
from stimmo.data import amenities, geocode
from stimmo.mcp import ratelimit, tools
from stimmo.models import CadastralCategory, RenovationTier, SellerType
from tests.conftest import COSTS_TODAY, parse_mcp_response

_ARGS = {"price_eur": 450_000, "prima_casa": True, "rendita_eur": 1_100}


@pytest.fixture()
async def http_client(_mcp_lifespan):
    """Own client IP so these calls never share a rate-limit bucket with other MCP tests."""
    from stimmo.web.app import application

    yield httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://localhost:8000",
        headers={
            "cf-connecting-ip": "127.0.0.3",
            "accept": "application/json, text/event-stream",
        },
    )


async def _initialize(client: httpx.AsyncClient) -> dict[str, str]:
    resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-costs", "version": "0.1"},
            },
            "id": 0,
        },
    )
    assert resp.status_code == 200, resp.text
    sid = resp.headers.get("mcp-session-id")
    return {"mcp-session-id": sid} if sid else {}


async def _rpc(client: httpx.AsyncClient, method: str, params: dict, headers: dict) -> dict:
    resp = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return parse_mcp_response(resp)


async def _call(client, headers, arguments) -> dict:
    data = await _rpc(
        client, "tools/call", {"name": "estimate_purchase_costs", "arguments": arguments}, headers
    )
    assert "result" in data, data
    return data["result"]


def _by_code(breakdown: dict, code: str) -> dict:
    hits = [ln for ln in breakdown["lines"] if ln["code"] == code]
    assert len(hits) == 1, (code, [ln["code"] for ln in breakdown["lines"]])
    return hits[0]


# ---------------------------------------------------------------------------
# Protocol level
# ---------------------------------------------------------------------------


async def test_tool_is_listed_with_a_derived_schema(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        data = await _rpc(client, "tools/list", {}, headers)

    tool = next(t for t in data["result"]["tools"] if t["name"] == "estimate_purchase_costs")
    props = tool["inputSchema"]["properties"]
    assert {"price_eur", "prima_casa", "seller", "category", "rendita_eur", "agency_pct"} <= set(
        props
    )
    assert {"mortgage_eur", "renovation_tier", "fine_condition", "surface_m2", "lang"} <= set(props)
    assert "mortgage_ltv_pct" not in tool["inputSchema"]["required"]
    assert "mortgage_ltv_pct" in props
    assert set(tool["inputSchema"]["required"]) == {"price_eur", "prima_casa"}
    # Enums come through as $defs, so a client can read the valid values from the schema.
    defs = tool["inputSchema"]["$defs"]
    assert set(defs["SellerType"]["enum"]) == {s.value for s in SellerType}
    assert set(defs["CadastralCategory"]["enum"]) == {c.value for c in CadastralCategory}
    assert set(defs["RenovationTier"]["enum"]) == {t.value for t in RenovationTier}


async def test_worked_example_through_the_endpoint(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(client, headers, {**_ARGS, "mortgage_eur": 300_000})

    assert result.get("isError") is not True, result
    breakdown = json.loads(result["content"][0]["text"])
    assert _by_code(breakdown, "registro_tax")["central_eur"] == 2_541
    assert _by_code(breakdown, "ipotecaria_catastale")["central_eur"] == 100
    assert _by_code(breakdown, "agency_fee")["central_eur"] == 16_470
    assert _by_code(breakdown, "substitute_tax_mortgage")["central_eur"] == 750
    assert breakdown["closing"]["central_eur"] > 28_000
    # Lines are structured (code + params) with provenance, never pre-rendered prose.
    line = _by_code(breakdown, "registro_tax")
    assert line["status"] == "statutory"
    assert line["source_url"].startswith("https://www.agenziaentrate.gov.it/")
    assert line["as_of"]


async def test_missing_rendita_is_bounded_and_flagged(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(client, headers, {"price_eur": 450_000, "prima_casa": True})

    breakdown = json.loads(result["content"][0]["text"])
    registro = _by_code(breakdown, "registro_tax")
    assert registro["status"] == "needs_input"
    assert (registro["low_eur"], registro["high_eur"]) == (1_000, 9_000)
    assert [n["code"] for n in breakdown["notes"]] == ["rendita_missing"]


async def test_fine_condition_picks_the_default_renovation_tier(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(
            client,
            headers,
            {**_ARGS, "surface_m2": 70, "fine_condition": "da ristrutturare"},
        )

    breakdown = json.loads(result["content"][0]["text"])
    assert _by_code(breakdown, "renovation_works")["params"]["tier"] == "full"
    assert any(ln["code"] == "renovation_bonus" for ln in breakdown["lines"])


async def test_explicit_tier_beats_fine_condition(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(
            client,
            headers,
            {
                **_ARGS,
                "surface_m2": 70,
                "fine_condition": "da ristrutturare",
                "renovation_tier": "light",
            },
        )

    breakdown = json.loads(result["content"][0]["text"])
    assert _by_code(breakdown, "renovation_works")["params"]["tier"] == "light"


async def test_invalid_input_is_a_structured_error(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(client, headers, {**_ARGS, "mortgage_eur": 900_000})

    assert result["isError"] is True
    text = result["content"][0]["text"]
    error = json.loads(text[text.find("{") :])
    assert error["code"] == "invalid_input"


async def test_mortgage_as_a_percentage_through_the_endpoint(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        by_ltv = await _call(client, headers, {**_ARGS, "mortgage_ltv_pct": 80})
        by_amount = await _call(client, headers, {**_ARGS, "mortgage_eur": 360_000})

    assert by_ltv.get("isError") is not True, by_ltv
    breakdown = json.loads(by_ltv["content"][0]["text"])
    # 80 % of 450,000 = 360,000; imposta sostitutiva 0.25 % (prima casa) = 900.
    assert breakdown["mortgage_eur"] == 360_000
    assert breakdown["mortgage_ltv_pct"] == 80
    assert breakdown["down_payment_eur"] == 90_000
    assert _by_code(breakdown, "substitute_tax_mortgage")["central_eur"] == 900
    # The two inputs are the same scenario.
    assert breakdown == json.loads(by_amount["content"][0]["text"])


async def test_both_amount_and_percentage_is_a_structured_error(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(
            client, headers, {**_ARGS, "mortgage_eur": 300_000, "mortgage_ltv_pct": 80}
        )

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert json.loads(text[text.find("{") :])["code"] == "invalid_input"


async def test_percentage_above_100_is_a_structured_error(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(client, headers, {**_ARGS, "mortgage_ltv_pct": 101})

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert json.loads(text[text.find("{") :])["code"] == "invalid_input"


async def test_ltv_notes_are_structured_and_sourced(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(
            client, headers, {"price_eur": 240_000, "prima_casa": True, "mortgage_ltv_pct": 100}
        )

    breakdown = json.loads(result["content"][0]["text"])
    notes = {n["code"]: n for n in breakdown["notes"]}
    assert {"ltv_above_limit", "consap_fund", "ltv_full_financing"} <= set(notes)
    assert "ltv_on_price_not_appraisal" in notes
    assert notes["ltv_above_limit"]["params"] == {"ltv_pct": 100, "limit_pct": 80, "max_pct": 100}
    assert notes["ltv_above_limit"]["source_url"].startswith("https://www.bancaditalia.it/")
    assert notes["consap_fund"]["source_url"] == "https://www.consap.it/fondo-prima-casa/"
    # Cash is split into the down payment and the costs on top of the price.
    assert breakdown["down_payment_eur"] == 0
    assert breakdown["cash_needed"]["central_eur"] == breakdown["total_on_top"]["central_eur"]


async def test_vocab_resources(http_client):
    expected = {
        "stimmo://vocab/seller-type": {e.value for e in SellerType},
        "stimmo://vocab/cadastral-category": {e.value for e in CadastralCategory},
        "stimmo://vocab/renovation-tier": {e.value for e in RenovationTier},
    }
    async with http_client as client:
        headers = await _initialize(client)
        listed = await _rpc(client, "resources/list", {}, headers)
        uris = {r["uri"] for r in listed["result"]["resources"]}
        assert set(expected) <= uris
        for uri, values in expected.items():
            data = await _rpc(client, "resources/read", {"uri": uri}, headers)
            items = json.loads(data["result"]["contents"][0]["text"])
            assert {i["value"] for i in items} == values
            assert all(i["label"] for i in items)


# ---------------------------------------------------------------------------
# Function level
# ---------------------------------------------------------------------------


async def test_tool_is_a_pure_calculation_with_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the costs tool must not touch the network")

    monkeypatch.setattr(geocode, "geocode", boom)
    monkeypatch.setattr(amenities, "fetch_amenities", boom)
    out = await tools.estimate_purchase_costs(price_eur=450_000, prima_casa=False)
    assert out["price_eur"] == 450_000
    assert out["total_on_top"]["central_eur"] > 0


async def test_tool_output_is_json_serialisable_and_matches_the_calculator():
    from stimmo.costs.calculator import compute
    from stimmo.models import PurchaseScenario

    out = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_eur=300_000
    )
    json.dumps(out)
    direct = compute(
        PurchaseScenario(
            price_eur=450_000,
            prima_casa=True,
            rendita_eur=1_100,
            mortgage_eur=300_000,
            as_of=COSTS_TODAY,
        )
    )
    assert out == direct.model_dump(mode="json")


async def test_tool_accepts_ltv_as_an_alternative_to_the_amount():
    by_ltv = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_ltv_pct=80
    )
    by_amount = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_eur=360_000
    )
    json.dumps(by_ltv)
    assert by_ltv == by_amount
    assert by_ltv["mortgage_eur"] == 360_000


async def test_tool_rejects_both_inputs_and_out_of_range_ltv():
    for kwargs in (
        {"mortgage_eur": 300_000, "mortgage_ltv_pct": 80},
        {"mortgage_ltv_pct": 100.5},
        {"mortgage_ltv_pct": -1},
    ):
        with pytest.raises(tools.ToolError) as exc:
            await tools.estimate_purchase_costs(price_eur=450_000, prima_casa=True, **kwargs)
        assert exc.value.code == "invalid_input", kwargs


async def test_a_default_amount_does_not_block_an_ltv():
    # Clients often send every parameter, defaults included.
    out = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, mortgage_eur=0, mortgage_ltv_pct=80
    )
    assert out["mortgage_eur"] == 360_000


async def test_tool_rejects_invalid_scenarios_as_tool_errors():
    with pytest.raises(tools.ToolError) as exc:
        await tools.estimate_purchase_costs(
            price_eur=450_000, prima_casa=True, renovation_tier=RenovationTier.FULL
        )  # renovation without a surface
    assert exc.value.code == "invalid_input"


# ---------------------------------------------------------------------------
# Mortgage payment: mortgage_years and mortgage_rate_pct
# ---------------------------------------------------------------------------

_PAYMENT_FIELDS = {
    "years",
    "months",
    "rate_pct",
    "monthly_payment_eur",
    "total_interest_eur",
    "total_repaid_eur",
    "mortgage_costs",
    "total_cost",
}


async def test_tool_returns_the_payment_fields():
    out = await tools.estimate_purchase_costs(
        price_eur=450_000,
        prima_casa=True,
        rendita_eur=1_100,
        mortgage_eur=360_000,
        mortgage_years=25,
        mortgage_rate_pct=3.0,
    )
    json.dumps(out)
    payment = out["mortgage_payment"]
    assert set(payment) == _PAYMENT_FIELDS
    assert payment["monthly_payment_eur"] == 1_707.16  # French amortisation, to the cent
    assert (payment["years"], payment["months"], payment["rate_pct"]) == (25, 300, 3.0)
    assert payment["total_repaid_eur"] == pytest.approx(
        360_000 + payment["total_interest_eur"], abs=0.01
    )
    # Total cost of the mortgage = interest + the mortgage-related lines already in closing.
    costs = sum(
        ln["central_eur"]
        for ln in out["lines"]
        if ln["code"]
        in {
            "substitute_tax_mortgage",
            "notary_mortgage",
            "mortgage_instruction_fee",
            "mortgage_appraisal",
            "fire_insurance",
        }
    )
    assert payment["mortgage_costs"]["central_eur"] == pytest.approx(costs, abs=0.01)
    assert payment["total_cost"]["central_eur"] == pytest.approx(
        payment["total_interest_eur"] + costs, abs=0.01
    )
    # The variable-rate caveat travels with the numbers.
    assert "mortgage_variable_rate" in {n["code"] for n in out["notes"]}


async def test_the_term_defaults_to_25_years_and_zero_rate_works():
    out = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, mortgage_ltv_pct=80, mortgage_rate_pct=0
    )
    payment = out["mortgage_payment"]
    assert payment["years"] == 25
    assert payment["monthly_payment_eur"] == 1_200.0  # 360,000 / 300
    assert payment["total_interest_eur"] == 0.0


async def test_no_rate_means_no_payment_and_a_note_not_a_guess():
    out = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, mortgage_eur=300_000, mortgage_years=20
    )
    assert out["mortgage_payment"] is None
    (note,) = [n for n in out["notes"] if n["code"] == "mortgage_rate_missing"]
    assert note["params"]["reference_pct"] == costs_rates.MORTGAGE_RATE_AVERAGE.central


async def test_no_mortgage_no_payment_even_with_a_rate():
    out = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, mortgage_rate_pct=3.0, mortgage_years=25
    )
    assert out["mortgage_payment"] is None


async def test_the_payment_does_not_move_the_cost_totals():
    base = await tools.estimate_purchase_costs(
        price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_eur=300_000
    )
    rated = await tools.estimate_purchase_costs(
        price_eur=450_000,
        prima_casa=True,
        rendita_eur=1_100,
        mortgage_eur=300_000,
        mortgage_rate_pct=3.5,
        mortgage_years=30,
    )
    for key in ("lines", "closing", "renovation", "total_on_top", "cash_needed"):
        assert rated[key] == base[key], key


async def test_tool_rejects_an_out_of_range_rate_or_term():
    for kwargs in (
        {"mortgage_rate_pct": -1},
        {"mortgage_rate_pct": 25},
        {"mortgage_years": 0},
        {"mortgage_years": 50},
    ):
        with pytest.raises(tools.ToolError) as exc:
            await tools.estimate_purchase_costs(
                price_eur=450_000, prima_casa=True, mortgage_eur=300_000, **kwargs
            )
        assert exc.value.code == "invalid_input", kwargs


async def test_payment_fields_through_the_endpoint(http_client):
    async with http_client as client:
        headers = await _initialize(client)
        out = await _call(
            client,
            headers,
            {**_ARGS, "mortgage_eur": 360_000, "mortgage_years": 25, "mortgage_rate_pct": 3.0},
        )
        schema = (await _rpc(client, "tools/list", {}, headers))["result"]["tools"]
    body = json.loads(out["content"][0]["text"])
    assert set(body["mortgage_payment"]) == _PAYMENT_FIELDS
    assert body["mortgage_payment"]["monthly_payment_eur"] == 1_707.16
    tool = next(t for t in schema if t["name"] == "estimate_purchase_costs")
    assert {"mortgage_years", "mortgage_rate_pct"} <= set(tool["inputSchema"]["properties"])
    assert not {"mortgage_years", "mortgage_rate_pct"} & set(tool["inputSchema"]["required"])


# ---------------------------------------------------------------------------
# Stale ranges: `stale` and `due` on every line
# ---------------------------------------------------------------------------

_SIX_MONTHS_ON = date(2027, 4, 7)  # day after the 6-month ranges fall due
_FULL = {**_ARGS, "mortgage_eur": 300_000, "surface_m2": 70, "renovation_tier": "medium"}


async def test_every_line_carries_stale_and_due():
    out = await tools.estimate_purchase_costs(price_eur=450_000, prima_casa=True, rendita_eur=1_100)
    assert out["lines"]
    for line in out["lines"]:
        assert line["stale"] is False  # frozen at COSTS_TODAY: all figures are in date
        assert "due" in line
    # Statutory lines have no review interval; benchmark lines state when they fall due.
    by_code = {ln["code"]: ln for ln in out["lines"]}
    assert by_code["registro_tax"]["due"] is None
    assert by_code["notary_purchase"]["due"] == "2027-10-06"
    assert not [n for n in out["notes"] if n["code"] == "ranges_overdue"]


async def test_overdue_ranges_are_flagged_with_their_due_date(monkeypatch):
    monkeypatch.setattr(tools, "_today", lambda: _SIX_MONTHS_ON)
    out = await tools.estimate_purchase_costs(
        price_eur=450_000,
        prima_casa=True,
        rendita_eur=1_100,
        mortgage_eur=300_000,
        surface_m2=70,
        renovation_tier=RenovationTier.MEDIUM,
    )
    json.dumps(out)
    by_code = {ln["code"]: ln for ln in out["lines"]}
    stale = {code for code, ln in by_code.items() if ln["stale"]}
    assert stale == {
        "mortgage_instruction_fee",
        "mortgage_appraisal",
        "fire_insurance",
        "renovation_works",
        "renovation_contingency",
        "renovation_fees",
    }
    assert by_code["mortgage_appraisal"]["due"] == "2027-04-06"
    # A line can be indicative and stale: `status` is not overloaded.
    assert by_code["renovation_works"]["status"] == "indicative"
    # Statutory lines never go stale, and notary (12 months) is still in date.
    for code in ("registro_tax", "substitute_tax_mortgage", "renovation_bonus"):
        assert by_code[code]["stale"] is False and by_code[code]["due"] is None
    assert by_code["notary_purchase"]["stale"] is False
    (note,) = [n for n in out["notes"] if n["code"] == "ranges_overdue"]
    assert note["params"] == {"count": 6, "last_reviewed": "2026-10-06"}


async def test_the_tool_reads_the_date_through_its_own_seam(monkeypatch):
    # Not the real clock: the same call at two frozen dates differs only in the flags.
    kwargs = dict(price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_eur=300_000)
    before = await tools.estimate_purchase_costs(**kwargs)
    monkeypatch.setattr(tools, "_today", lambda: date(2027, 10, 7))
    after = await tools.estimate_purchase_costs(**kwargs)
    assert before["total_on_top"] == after["total_on_top"]
    assert not any(ln["stale"] for ln in before["lines"])
    assert all(ln["stale"] for ln in after["lines"] if ln["due"] is not None)


async def test_stale_and_due_through_the_endpoint(http_client, monkeypatch):
    monkeypatch.setattr(tools, "_today", lambda: _SIX_MONTHS_ON)
    async with http_client as client:
        headers = await _initialize(client)
        result = await _call(client, headers, _FULL)

    assert result.get("isError") is not True, result
    breakdown = json.loads(result["content"][0]["text"])
    appraisal = _by_code(breakdown, "mortgage_appraisal")
    assert appraisal["stale"] is True and appraisal["due"] == "2027-04-06"
    assert _by_code(breakdown, "registro_tax")["stale"] is False
    assert "ranges_overdue" in [n["code"] for n in breakdown["notes"]]


# ---------------------------------------------------------------------------
# Rate limiting: cheap tier
# ---------------------------------------------------------------------------


def test_tool_is_in_the_cheap_rate_limit_tier():
    assert "estimate_purchase_costs" not in ratelimit._EXPENSIVE_TOOLS
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "estimate_purchase_costs", "arguments": _ARGS},
            "id": 1,
        }
    ).encode()
    assert ratelimit._is_expensive_call(body) is False
