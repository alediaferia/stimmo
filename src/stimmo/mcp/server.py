from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from stimmo.mcp import resources as _res
from stimmo.mcp import tools as _tools
from stimmo.mcp.prompts import appraise_listing as _appraise_fn
from stimmo.mcp.ratelimit import RateLimitMiddleware

mcp = FastMCP(
    "stimmo",
    instructions=(
        "Milan property valuation via OMI quotations. "
        "Use estimate_property for a full appraisal or the partial tools for individual steps. "
        "Consult stimmo://vocab/* resources for valid enum values before calling tools."
    ),
    stateless_http=True,
)

# ── Tools (register typed functions directly so FastMCP derives the schema) ─

mcp.tool(description="Full Milan listing appraisal: geocode, OMI quote, amenity score, verdict.")(
    _tools.estimate_property
)
mcp.tool(description="Return the OMI €/m² band for an address without running a full estimate.")(
    _tools.lookup_omi_quote
)
mcp.tool(
    description="Geocode a Milan address to (lat, lon) and verify it falls inside the comune."
)(_tools.geocode_milan_address)
mcp.tool(description="Return the OMI zone (code + description) for a (lat, lon) coordinate.")(
    _tools.omi_zone_for_point
)
mcp.tool(
    description="Return the amenity proximity score (transit, parks, shops) for a (lat, lon)."
)(_tools.amenity_score)
mcp.tool(description="Return the 8-semester OMI €/m² history series for a zone and property type.")(
    _tools.omi_history
)

# ── Resources ──────────────────────────────────────────────────────────────


@mcp.resource("stimmo://semester")
def semester_resource() -> str:
    return _res.semester_resource()


@mcp.resource("stimmo://vocab/property-type")
def property_type_vocab() -> str:
    return _res.property_type_vocab()


@mcp.resource("stimmo://vocab/fine-condition")
def fine_condition_vocab() -> str:
    return _res.fine_condition_vocab()


@mcp.resource("stimmo://vocab/energy-class")
def energy_class_vocab() -> str:
    return _res.energy_class_vocab()


@mcp.resource("stimmo://vocab/outdoor")
def outdoor_vocab() -> str:
    return _res.outdoor_vocab()


@mcp.resource("stimmo://vocab/construction-era")
def construction_era_vocab() -> str:
    return _res.construction_era_vocab()


@mcp.resource("stimmo://vocab/orientation")
def orientation_vocab() -> str:
    return _res.orientation_vocab()


@mcp.resource("stimmo://vocab/exposure")
def exposure_vocab() -> str:
    return _res.exposure_vocab()


@mcp.resource("stimmo://omi-zones")
def omi_zones_resource() -> str:
    return _res.omi_zones_resource()


# ── Prompts ────────────────────────────────────────────────────────────────


@mcp.prompt(description="Given a Milan listing URL or free-text description, appraise it.")
def appraise_listing(listing: str) -> str:
    return _appraise_fn(listing)


# ── Factory ────────────────────────────────────────────────────────────────

# Build once so the session manager is a stable singleton.
_streamable_app = mcp.streamable_http_app()
_session_manager = _streamable_app.routes[0].app.session_manager


def get_session_manager():
    """Return the MCP session manager (needs .run() via app lifespan)."""
    return _session_manager


def build_mcp_app():  # type: ignore[return]
    """Return the Streamable-HTTP ASGI app wrapped in per-IP rate-limit middleware."""
    return RateLimitMiddleware(_streamable_app)
