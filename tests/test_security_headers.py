"""Tests for the baseline security-header middleware wrapping `application`.

These assert against `stimmo.web.app.application` (the ASGI callable wrapped by
both metrics.instrument and the security-headers middleware), not the bare
FastAPI `app`, since the headers are injected at that outer layer — see
web/app.py's `_with_security_headers` / `application = ...` assembly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stimmo.web.app import _CSP, application


@pytest.fixture()
def client() -> TestClient:
    return TestClient(application, follow_redirects=False)


EXPECTED_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "content-security-policy": _CSP,
}


def _assert_security_headers(headers) -> None:
    for name, value in EXPECTED_HEADERS.items():
        assert headers.get(name) == value, f"missing/wrong {name!r}: {headers.get(name)!r}"


def test_html_page_carries_security_headers(client: TestClient):
    r = client.get("/it/")
    assert r.status_code == 200
    _assert_security_headers(r.headers)


def test_json_route_carries_security_headers(client: TestClient):
    # Locale-neutral JSON API endpoint — too-short query still returns a real
    # (400) HTTP response with headers rather than erroring before _dispatch.
    r = client.get("/api/geocode", params={"q": "a"})
    assert r.status_code == 400
    assert r.headers.get("content-type", "").startswith("application/json")
    _assert_security_headers(r.headers)


def test_redirect_carries_security_headers(client: TestClient):
    # Bare-path redirect — headers must apply to every response, not just 200s.
    r = client.get("/")
    assert r.status_code == 301
    _assert_security_headers(r.headers)


def test_csp_allows_only_grepped_origins():
    """Pin the CSP string's shape so a future edit has to be deliberate: every
    non-'self'/'none' origin here must be traceable to a template/static grep
    (see the comment above `_CSP` in web/app.py)."""
    assert "default-src 'self'" in _CSP
    assert "https://static.cloudflareinsights.com" in _CSP  # base.html CF beacon <script>
    assert "https://cloudflareinsights.com" in _CSP  # CF beacon report endpoint
    assert "https://*.basemaps.cartocdn.com" in _CSP  # Leaflet tile layers
    assert "data:" in _CSP  # vendor/leaflet/leaflet.js inline GIF fallback
    assert "frame-ancestors 'none'" in _CSP
    assert "object-src 'none'" in _CSP
    assert "fonts.googleapis.com" not in _CSP
    assert "fonts.gstatic.com" not in _CSP
