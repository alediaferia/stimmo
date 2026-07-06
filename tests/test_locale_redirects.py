from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stimmo.web.app import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


class TestBarePathRedirects:
    """Bare paths (no /{lang}/ prefix) must redirect, not 422."""

    def test_root_redirects(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 301
        assert r.headers["location"] in ("/it/", "/en/")

    def test_about_redirects(self, client: TestClient):
        r = client.get("/about")
        assert r.status_code == 301
        assert r.headers["location"] in ("/it/about", "/en/about")

    def test_bookmarklet_redirects(self, client: TestClient):
        r = client.get("/bookmarklet")
        assert r.status_code == 301
        assert r.headers["location"] in ("/it/bookmarklet", "/en/bookmarklet")

    def test_import_redirects(self, client: TestClient):
        r = client.get("/import")
        assert r.status_code == 302
        assert r.headers["location"] in ("/it/import", "/en/import")

    def test_import_preserves_query_string(self, client: TestClient):
        r = client.get("/import?src=foo&v=1")
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("/it/import") or loc.startswith("/en/import")
        assert "src=foo" in loc

    def test_unknown_path_catch_all(self, client: TestClient):
        r = client.get("/nonexistent-page")
        assert r.status_code == 301
        assert r.headers["location"] in ("/it/nonexistent-page", "/en/nonexistent-page")

    def test_lang_prefixed_unknown_path_404s(self, client: TestClient):
        # Regression: catch-all must not redirect /it/foo → /it/it/foo.
        r = client.get("/it/favicon.ico")
        assert r.status_code == 404
        r = client.get("/en/nonexistent")
        assert r.status_code == 404


class TestLangCookieNegotiation:
    """stimmo_lang cookie must control the redirect target."""

    def test_cookie_it(self, client: TestClient):
        client.cookies.set("stimmo_lang", "it")
        r = client.get("/about")
        assert r.headers["location"] == "/it/about"

    def test_cookie_en(self, client: TestClient):
        client.cookies.set("stimmo_lang", "en")
        r = client.get("/about")
        assert r.headers["location"] == "/en/about"
