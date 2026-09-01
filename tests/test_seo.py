"""Tests for the Phase A distribution/SEO work: robots.txt, sitemap.xml, canonical/
hreflang/JSON-LD head tags, permanent redirects, HEAD support, the privacy page, and
the env-gated Cloudflare analytics beacon."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from stimmo.data import neighborhoods as nb_module
from stimmo.data import omi, zones
from stimmo.web import app as app_module
from stimmo.web.app import (
    SeoRoute,
    _release_date,
    _semester_start_date,
    _seo_urls,
    _sitemap_xml,
    app,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def _fake_request(
    path: str = "/it/fake", scheme: str = "https", host: str = "stimmo.it"
) -> Request:
    """A bare Starlette Request for calling _seo_urls() directly, without going
    through a full HTTP round-trip. Registered-route lookups in _seo_urls only use
    request.url.scheme/netloc (never .path), so `path` is irrelevant unless the
    caller falls back to route=None."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", host.encode())],
        "scheme": scheme,
        "server": (host, 443 if scheme == "https" else 80),
        "client": ("testclient", 123),
        "app": None,
    }
    return Request(scope)


@pytest.fixture()
def seo_registry_sandbox(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Isolates _SEO_ROUTES mutations to a single test. Registering a throwaway
    route via monkeypatch.setitem auto-reverts _SEO_ROUTES on teardown; clearing
    _sitemap_xml's lru_cache on both sides of the test stops a fake route from
    leaking into (or a stale sitemap from leaking out of) any other test, whatever
    order pytest happens to run them in."""
    _sitemap_xml.cache_clear()
    yield monkeypatch
    _sitemap_xml.cache_clear()


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


class TestSitemapLastmod:
    """R2: every URL's <lastmod> must derive from whatever actually determines that
    URL's content (CHANGELOG.md's release date for static template pages, the
    bundled OMI semester for the OMI-band pages, the content file's per-neighborhood
    `updated` date otherwise) — never build/deploy time, and never a fabricated
    date when the real one is unknown. See SeoRoute.lastmod and the three supplier
    functions in web/app.py."""

    def _lastmod_after(self, body: str, loc: str) -> str | None:
        i = body.find(f"<loc>{loc}</loc>")
        assert i != -1, f"{loc} not found in sitemap"
        m = re.match(r"\s*<lastmod>([^<]+)</lastmod>", body[i + len(f"<loc>{loc}</loc>") :])
        return m.group(1) if m else None

    def test_static_page_lastmod_is_the_release_date(self, client: TestClient):
        expected = _release_date()
        assert expected is not None, "CHANGELOG.md must have at least one version heading"
        body = client.get("/sitemap.xml").text
        assert self._lastmod_after(body, "https://stimmo.it/it/about") == expected.isoformat()
        assert self._lastmod_after(body, "https://stimmo.it/en/privacy") == expected.isoformat()

    def test_zone_pages_lastmod_is_the_omi_semester_start(self, client: TestClient):
        expected = _semester_start_date(omi.semester())
        assert expected is not None
        body = client.get("/sitemap.xml").text
        assert self._lastmod_after(body, "https://stimmo.it/it/zones") == expected.isoformat()
        code, _descr = zones.list_zones()[0]
        loc = f"https://stimmo.it/it/zones/{code}"
        assert self._lastmod_after(body, loc) == expected.isoformat()

    def test_every_emitted_lastmod_is_a_valid_iso_date(self, client: TestClient):
        body = client.get("/sitemap.xml").text
        found = re.findall(r"<lastmod>([^<]+)</lastmod>", body)
        assert found  # sanity: static + zone pages always emit one
        for raw in found:
            date.fromisoformat(raw)  # raises ValueError if malformed

    def test_neighborhood_without_updated_field_omits_lastmod(
        self, client: TestClient, empty_content_dir: Path
    ):
        n = nb_module.neighborhood_for_slug("brera", "it")
        content = {n.slug_en: {"blurb_it": "Testo editoriale.", "blurb_en": "Editorial copy."}}
        (empty_content_dir / "neighborhoods.json").write_text(json.dumps(content), encoding="utf-8")
        nb_module._neighborhoods_with_content.cache_clear()
        _sitemap_xml.cache_clear()

        body = client.get("/sitemap.xml").text
        loc = f"https://stimmo.it/it/milano/{n.slug_it}-prezzi-al-mq"
        assert self._lastmod_after(body, loc) is None

    def test_neighborhood_with_updated_field_emits_it_as_lastmod(
        self, client: TestClient, empty_content_dir: Path
    ):
        n = nb_module.neighborhood_for_slug("brera", "it")
        content = {
            n.slug_en: {
                "blurb_it": "Testo editoriale.",
                "blurb_en": "Editorial copy.",
                "updated": "2026-08-26",
            }
        }
        (empty_content_dir / "neighborhoods.json").write_text(json.dumps(content), encoding="utf-8")
        nb_module._neighborhoods_with_content.cache_clear()
        _sitemap_xml.cache_clear()

        body = client.get("/sitemap.xml").text
        loc = f"https://stimmo.it/it/milano/{n.slug_it}-prezzi-al-mq"
        assert self._lastmod_after(body, loc) == "2026-08-26"


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

    # Regression coverage for the bug this class didn't catch before: every
    # `@app.get("/{lang}/...")` route was GET-only, so a HEAD request path-matched
    # but method-missed it (Starlette Match.PARTIAL) and fell through to the
    # catch-all `bare_path_redirect` (registered GET+HEAD), which 404s a
    # lang-prefixed path on purpose. Fixed by `_HeadableAPIRoute` in web/app.py,
    # set as `app.router.route_class` so every GET route gains HEAD automatically.
    @pytest.mark.parametrize("path", ["/it/", "/en/", "/it/zones", "/en/zones", "/it/about"])
    def test_head_lang_prefixed_route_is_200_not_404(self, client: TestClient, path: str):
        r = client.head(path)
        assert r.status_code == 200

    def test_head_zone_detail_is_200(self, client: TestClient):
        code, _descr = zones.list_zones()[0]
        r = client.head(f"/it/zones/{code}")
        assert r.status_code == 200

    def test_head_content_length_matches_get_and_body_is_empty(self, client: TestClient):
        get_resp = client.get("/it/zones")
        head_resp = client.head("/it/zones")
        assert head_resp.status_code == 200
        assert head_resp.headers["content-length"] == get_resp.headers["content-length"]
        assert head_resp.content == b""

    def test_head_lang_prefixed_route_labels_metrics_by_real_endpoint(self):
        # Goes through `application` (app_module.application), not the bare FastAPI
        # `app` — metrics are recorded by `_metrics.instrument`, which wraps
        # `_dispatch`, not `app` itself, so the module-level `client` fixture
        # (bound directly to `app`) wouldn't exercise the middleware at all.
        #
        # The bug this guards: before the fix, scope["endpoint"] for a HEAD request
        # against /it/zones was bare_path_redirect (the catch-all that won the
        # partial-match race), not zones_index — so this counter sample would not
        # exist under the "zones_index" route label at all.
        from stimmo.web.metrics import REQUESTS

        metrics_client = TestClient(app_module.application, follow_redirects=False)
        before = REQUESTS.labels(method="HEAD", route="zones_index", status="200")._value.get()
        r = metrics_client.head("/it/zones")
        assert r.status_code == 200
        after = REQUESTS.labels(method="HEAD", route="zones_index", status="200")._value.get()
        assert after == before + 1


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
        assert "application/ld+json" in body
        assert '"@type": "WebApplication"' in body
        assert '"name": "stimmo"' in body

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_json_ld_on_about(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/about").text
        assert "application/ld+json" in body
        assert '"@type": "WebApplication"' in body

    # Found while implementing R1 (2026-08-30 SEO review): base.html emitted a
    # site-wide default <meta name="description"> *outside* the overridable
    # og_meta block, so every page that overrode og_meta with its own description
    # (zones_index, zone_detail, neighborhood_detail) rendered *two* competing
    # <meta name="description"> tags — the generic one first, in DOM order, which
    # is what a crawler that only reads the first tag would actually index. Fixed
    # by moving the default description inside the og_meta block itself, matching
    # how og:description/twitter:description already worked. Guards every page
    # type that renders a head (form, about, privacy, bookmarklet, zones index/
    # detail, neighborhood detail) so this can't regress silently on either side —
    # zero tags would be as wrong as two.
    @pytest.mark.parametrize(
        "path",
        [
            "/it/",
            "/it/about",
            "/it/privacy",
            "/it/bookmarklet",
            "/it/zones",
            "/it/zones/B12",
        ],
    )
    def test_exactly_one_meta_description_per_page(self, client: TestClient, path: str):
        body = client.get(path).text
        assert len(re.findall(r'<meta name="description"', body)) == 1


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


# ---------------------------------------------------------------------------
# SEO route registry refactor: _seo_urls / _sitemap_xml now both read the single
# _SEO_ROUTES registry (app.py). These tests cover the registry contract directly
# — including two hypothetical routes registered only for the duration of one test
# (see the `seo_registry_sandbox` fixture above) — plus a byte-for-byte regression
# snapshot of the sitemap's URL set.
# ---------------------------------------------------------------------------


@pytest.fixture()
def empty_content_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pins STIMMO_CONTENT_DIR at an empty tmp_path (no neighborhoods.json written)
    so this class's URL-set snapshot is deterministic regardless of what editorial
    content happens to be on disk locally or in prod — now that neighborhood blurbs
    are loadable from an external file, the snapshot must not depend on whether one
    happens to exist. Yields the tmp_path so a test can drop a file into it and
    re-clear the caches itself; see `test_fully_blurbed_neighborhood_adds_exactly_
    its_two_urls` below. The content-injecting counterpart (proving the *iff* rule
    end to end) lives in test_neighborhood_pages.py's `content_dir` fixture.
    """
    monkeypatch.setenv("STIMMO_CONTENT_DIR", str(tmp_path))
    nb_module._neighborhoods_with_content.cache_clear()
    _sitemap_xml.cache_clear()
    yield tmp_path
    nb_module._neighborhoods_with_content.cache_clear()
    _sitemap_xml.cache_clear()


class TestSitemapRegressionSnapshot:
    """The non-neighborhood sitemap surface is 96 URLs: 5 static paths x 2 langs,
    plus every OMI zone code x 2 langs. This is the regression bar for the registry
    refactor — the exact same URL set, computed independently of _sitemap_xml's
    implementation, and pinned to an empty content dir so it can never drift just
    because a neighborhood blurb was dropped into STIMMO_CONTENT_DIR somewhere. Only
    the iff-both-blurbs rule governs whether neighborhood URLs are added on top of
    this set — see test_url_set_and_count_unchanged (0 neighborhoods qualify here)
    and test_fully_blurbed_neighborhood_adds_exactly_its_two_urls (exactly 1 does)."""

    def test_url_set_and_count_unchanged(self, client: TestClient, empty_content_dir: Path):
        body = client.get("/sitemap.xml").text
        locs = set(re.findall(r"<loc>(.*?)</loc>", body))

        expected: set[str] = set()
        for lang in ("it", "en"):
            expected.add(f"https://stimmo.it/{lang}/")
        for path in ("/about", "/bookmarklet", "/privacy", "/zones"):
            for lang in ("it", "en"):
                expected.add(f"https://stimmo.it/{lang}{path}")
        for code, _descr in zones.list_zones():
            for lang in ("it", "en"):
                expected.add(f"https://stimmo.it/{lang}/zones/{code}")

        assert locs == expected
        assert len(locs) == 96

    def test_fully_blurbed_neighborhood_adds_exactly_its_two_urls(
        self, client: TestClient, empty_content_dir: Path
    ):
        n = nb_module.neighborhood_for_slug("brera", "it")
        content = {n.slug_en: {"blurb_it": "Testo editoriale.", "blurb_en": "Editorial copy."}}
        (empty_content_dir / "neighborhoods.json").write_text(json.dumps(content), encoding="utf-8")
        nb_module._neighborhoods_with_content.cache_clear()
        _sitemap_xml.cache_clear()

        body = client.get("/sitemap.xml").text
        locs = set(re.findall(r"<loc>(.*?)</loc>", body))

        assert len(locs) == 98
        assert f"https://stimmo.it/it/milano/{n.slug_it}-prezzi-al-mq" in locs
        assert f"https://stimmo.it/en/milan/{n.slug_en}-property-prices" in locs


class TestSeoRouteRegistryContract:
    def test_single_language_route_emits_only_self_referential_alternate(
        self, seo_registry_sandbox: pytest.MonkeyPatch
    ):
        route = SeoRoute(
            key="en_only_guide",
            endpoint="fake_guide_endpoint",
            suffixes={"en": "/guides/only-in-english"},
        )
        seo_registry_sandbox.setitem(app_module._SEO_ROUTES, "en_only_guide", route)

        result = _seo_urls(_fake_request(), "en", route="en_only_guide")

        assert result["hreflang_alternates"] == {
            "en": "https://stimmo.it/en/guides/only-in-english"
        }
        assert "it" not in result["hreflang_alternates"]
        assert result["hreflang_x_default"] == "https://stimmo.it/en/guides/only-in-english"
        assert result["canonical_url"] == "https://stimmo.it/en/guides/only-in-english"

    def test_single_language_route_404s_under_the_missing_language(
        self, seo_registry_sandbox: pytest.MonkeyPatch
    ):
        route = SeoRoute(
            key="en_only_guide",
            endpoint="fake_guide_endpoint",
            suffixes={"en": "/guides/only-in-english"},
        )
        seo_registry_sandbox.setitem(app_module._SEO_ROUTES, "en_only_guide", route)

        with pytest.raises(HTTPException) as exc_info:
            _seo_urls(_fake_request(), "it", route="en_only_guide")
        assert exc_info.value.status_code == 404

    def test_per_language_slug_route_emits_correct_per_language_alternates(
        self, seo_registry_sandbox: pytest.MonkeyPatch
    ):
        # Mirrors the neighborhood-pages case this refactor exists for:
        # /en/milan/brera-property-prices vs /it/milano/brera-prezzi-al-mq.
        route = SeoRoute(
            key="neighborhood_brera",
            endpoint="fake_neighborhood_endpoint",
            suffixes={"it": "/milano/{slug_it}", "en": "/milan/{slug_en}"},
        )
        seo_registry_sandbox.setitem(app_module._SEO_ROUTES, "neighborhood_brera", route)
        params = {"slug_it": "brera-prezzi-al-mq", "slug_en": "brera-property-prices"}

        result = _seo_urls(_fake_request(), "en", route="neighborhood_brera", params=params)

        assert result["hreflang_alternates"] == {
            "it": "https://stimmo.it/it/milano/brera-prezzi-al-mq",
            "en": "https://stimmo.it/en/milan/brera-property-prices",
        }
        assert result["canonical_url"] == "https://stimmo.it/en/milan/brera-property-prices"
        # "it" exists for this route, so x-default follows it, per the registry's rule.
        assert result["hreflang_x_default"] == "https://stimmo.it/it/milano/brera-prezzi-al-mq"

    @pytest.mark.parametrize(("scheme", "host"), [("https", "stimmo.it"), ("http", "testserver")])
    def test_canonical_is_absolute_scheme_derived_and_self_referential(
        self, scheme: str, host: str
    ):
        for lang in ("it", "en"):
            result = _seo_urls(_fake_request(scheme=scheme, host=host), lang, route="about")
            assert result["canonical_url"] == f"{scheme}://{host}/{lang}/about"
            assert result["hreflang_alternates"][lang] == result["canonical_url"]


class TestEveryHtmlEndpointHasASeoDecision:
    """Guards against a new HTML-rendering route silently inheriting _seo_urls's
    lang-invariant fallback (the exact bug this refactor fixes). A new endpoint must
    either register a SeoRoute (real per-language hreflang/canonical/sitemap
    treatment) or land in _SEO_MIRRORED_ENDPOINTS (an explicit, reviewed opt-in to
    the fallback for non-indexable pages). Landing in neither fails this test."""

    def test_every_html_endpoint_has_a_seo_decision(self):
        html_endpoints = {
            route.endpoint.__name__
            for route in app.routes
            if isinstance(route, APIRoute) and route.response_class is HTMLResponse
        }
        registered = {r.endpoint for r in app_module._SEO_ROUTES.values()}
        decided = registered | app_module._SEO_MIRRORED_ENDPOINTS
        assert html_endpoints == decided
