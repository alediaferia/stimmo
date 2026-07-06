"""Tests for the Phase A distribution/SEO work: robots.txt, sitemap.xml, canonical/
hreflang/JSON-LD head tags, permanent redirects, HEAD support, the privacy page, and
the env-gated Cloudflare analytics beacon."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stimmo.web.app import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# WP-2: robots.txt + sitemap.xml
# ---------------------------------------------------------------------------


class TestRobotsTxt:
    def test_status_and_content_type(self, client: TestClient):
        r = client.get("/robots.txt")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")

    def test_allows_all_and_lists_sitemap(self, client: TestClient):
        r = client.get("/robots.txt")
        body = r.text
        assert "User-agent: *" in body
        assert "Allow: /" in body
        assert "Sitemap: https://stimmo.it/sitemap.xml" in body

    def test_allows_ai_crawlers(self, client: TestClient):
        body = client.get("/robots.txt").text
        for bot in ("GPTBot", "ClaudeBot", "CCBot", "Google-Extended", "meta-externalagent"):
            assert bot in body

    def test_head_supported(self, client: TestClient):
        r = client.head("/robots.txt")
        assert r.status_code == 200

    def test_registered_before_catch_all(self, client: TestClient):
        # If bare_path_redirect ever shadowed this route, we'd get a 301/302
        # redirect instead of a direct 200.
        r = client.get("/robots.txt")
        assert r.status_code == 200


class TestSitemapXml:
    def test_status_and_content_type(self, client: TestClient):
        r = client.get("/sitemap.xml")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/xml")

    def test_contains_indexable_pages_both_langs(self, client: TestClient):
        body = client.get("/sitemap.xml").text
        for lang in ("it", "en"):
            assert f"https://stimmo.it/{lang}/</loc>" in body
            assert f"https://stimmo.it/{lang}/about</loc>" in body
            assert f"https://stimmo.it/{lang}/bookmarklet</loc>" in body
            assert f"https://stimmo.it/{lang}/privacy</loc>" in body

    def test_hreflang_alternates_present(self, client: TestClient):
        body = client.get("/sitemap.xml").text
        assert 'hreflang="it"' in body
        assert 'hreflang="en"' in body

    def test_excludes_share_and_og_routes(self, client: TestClient):
        body = client.get("/sitemap.xml").text
        assert "/s/" not in body
        assert "/og/" not in body

    def test_head_supported(self, client: TestClient):
        r = client.head("/sitemap.xml")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# WP-3: permanent redirects + HEAD support
# ---------------------------------------------------------------------------


class TestPermanentRedirects:
    def test_root_is_301(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 301

    def test_about_is_301(self, client: TestClient):
        r = client.get("/about")
        assert r.status_code == 301

    def test_bookmarklet_is_301(self, client: TestClient):
        r = client.get("/bookmarklet")
        assert r.status_code == 301

    def test_bare_path_catch_all_is_301(self, client: TestClient):
        r = client.get("/nonexistent-page")
        assert r.status_code == 301

    def test_import_get_untouched_302(self, client: TestClient):
        # Explicitly out of scope for the 301 migration.
        r = client.get("/import")
        assert r.status_code == 302

    def test_import_post_untouched_308(self, client: TestClient):
        r = client.post("/import", data={})
        assert r.status_code == 308

    def test_set_lang_untouched_302(self, client: TestClient):
        r = client.post("/set-lang", data={"lang": "it", "next": "/it/"})
        assert r.status_code == 302


class TestHeadSupport:
    def test_head_root(self, client: TestClient):
        r = client.head("/")
        assert r.status_code == 301

    def test_head_about(self, client: TestClient):
        r = client.head("/about")
        assert r.status_code == 301

    def test_head_bookmarklet(self, client: TestClient):
        r = client.head("/bookmarklet")
        assert r.status_code == 301

    def test_head_bare_catch_all(self, client: TestClient):
        r = client.head("/somewhere")
        assert r.status_code == 301


# ---------------------------------------------------------------------------
# WP-3: canonical / hreflang / JSON-LD in rendered pages
# ---------------------------------------------------------------------------


class TestHeadTags:
    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_canonical_and_hreflang_on_home(self, client: TestClient, lang: str):
        r = client.get(f"/{lang}/")
        assert r.status_code == 200
        body = r.text
        assert f'rel="canonical" href="http://testserver/{lang}/"' in body
        assert 'hreflang="it"' in body
        assert 'hreflang="en"' in body
        assert 'hreflang="x-default"' in body

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_canonical_and_hreflang_on_about(self, client: TestClient, lang: str):
        r = client.get(f"/{lang}/about")
        assert r.status_code == 200
        body = r.text
        assert f'rel="canonical" href="http://testserver/{lang}/about"' in body

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_json_ld_on_home(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/").text
        assert 'application/ld+json' in body
        assert '"@type": "WebApplication"' in body
        assert '"name": "stimmo"' in body

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_json_ld_on_about(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/about").text
        assert 'application/ld+json' in body
        assert '"@type": "WebApplication"' in body


# ---------------------------------------------------------------------------
# WP-4: privacy page
# ---------------------------------------------------------------------------


class TestPrivacyPage:
    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_privacy_page_200(self, client: TestClient, lang: str):
        r = client.get(f"/{lang}/privacy")
        assert r.status_code == 200

    def test_privacy_linked_from_footer(self, client: TestClient):
        body = client.get("/it/").text
        assert 'href="/it/privacy"' in body

    def test_privacy_in_sitemap(self, client: TestClient):
        body = client.get("/sitemap.xml").text
        assert "https://stimmo.it/it/privacy</loc>" in body
        assert "https://stimmo.it/en/privacy</loc>" in body


# ---------------------------------------------------------------------------
# WP-5: analytics beacon (env-gated)
# ---------------------------------------------------------------------------


class TestAnalyticsBeacon:
    def test_no_beacon_when_unset(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("STIMMO_CF_BEACON_TOKEN", raising=False)
        body = client.get("/it/").text
        assert "cloudflareinsights.com/beacon.min.js" not in body

    def test_beacon_when_set(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("STIMMO_CF_BEACON_TOKEN", "test-token-123")
        body = client.get("/it/").text
        assert "cloudflareinsights.com/beacon.min.js" in body
        assert '"token": "test-token-123"' in body
