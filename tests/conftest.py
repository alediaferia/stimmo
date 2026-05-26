"""Shared pytest infrastructure for stimmo tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest


@asynccontextmanager
async def run_lifespan(app):
    """Run the ASGI lifespan of *app* in a background asyncio task.

    anyio cancel scopes created during the lifespan (e.g. by
    StreamableHTTPSessionManager.run()) are entered and exited entirely
    within that background task, avoiding pytest-asyncio 1.x cross-task
    cancel-scope errors.

    NOTE: StreamableHTTPSessionManager.run() can only be called once per
    instance. Use the session-scoped _mcp_lifespan fixture rather than calling
    this directly from function-scoped fixtures.
    """
    startup_complete = asyncio.Event()
    shutdown_requested = asyncio.Event()
    started = False

    async def receive():
        nonlocal started
        if not started:
            started = True
            return {"type": "lifespan.startup"}
        await shutdown_requested.wait()
        return {"type": "lifespan.shutdown"}

    async def send(message):
        if message["type"] == "lifespan.startup.complete":
            startup_complete.set()

    scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
    task = asyncio.create_task(app(scope, receive, send))
    await startup_complete.wait()

    try:
        yield
    finally:
        shutdown_requested.set()
        try:
            await task
        except Exception:
            pass


def parse_mcp_response(resp: httpx.Response) -> dict:
    """Parse a MCP HTTP response that may be plain JSON or SSE-wrapped JSON.

    Streamable HTTP always wraps single-message responses in an SSE event:
        event: message\\r\\ndata: <json>\\r\\n\\r\\n
    """
    ct = resp.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise ValueError(f"No data line in SSE response: {resp.text!r}")
    return resp.json()


@pytest.fixture(scope="session")
async def _mcp_lifespan():
    """Start the MCP session manager exactly once for the entire test session.

    StreamableHTTPSessionManager.run() can only be called once per instance,
    so the lifespan must be session-scoped.  asyncio_default_fixture_loop_scope
    = "session" in pyproject.toml ensures all async fixtures (including
    function-scoped http_client) share the same event loop.
    """
    from stimmo.web.app import application

    async with run_lifespan(application):
        yield


@pytest.fixture()
async def http_client(_mcp_lifespan):
    """AsyncClient backed by the real ASGI app with MCP session manager running (IP 127.0.0.1)."""
    from stimmo.web.app import application

    yield httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://localhost:8000",
        headers={
            "cf-connecting-ip": "127.0.0.1",
            "accept": "application/json, text/event-stream",
        },
    )
