"""Tests for the brand-SERP disambiguation work: site-wide Organization JSON-LD
(base.html) plus the WebSite node on the home page, and the strengthened /about
entity statement.

Covers: the Organization node renders on every page — including ones that override
`{% block json_ld %}` with their own page-type markup (form.html/about.html use
WebApplication, zone_detail.html uses Place) — every emitted JSON-LD block is valid,
self-describing JSON, `sameAs` lists only the canonical GitHub repo, and the `logo`
URL is absolute https and points at a file that actually exists in web/static/.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from stimmo.web.app import STATIC_DIR, app

JSON_LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


@pytest.fixture()
def client() -> TestClient:
    # Forced https + a fixed host so `request.base_url`-derived absolute URLs
    # (Organization.url, Organization.logo.url, WebSite.url) are deterministic
    # and so the "absolute https" assertions below are meaningful — the default
    # TestClient origin is plain http://testserver.
    return TestClient(app, base_url="https://stimmo.it", follow_redirects=False)


def _json_ld_blocks(body: str) -> list[dict]:
    blocks = [json.loads(m.group(1)) for m in JSON_LD_RE.finditer(body)]
    assert blocks, "expected at least one application/ld+json block"
    return blocks


def _organization_node(blocks: list[dict]) -> dict:
    orgs = [b for b in blocks if b.get("@type") == "Organization"]
    assert len(orgs) == 1, f"expected exactly one Organization node, got {len(orgs)}"
    return orgs[0]


# A page from each family: the two that already override json_ld with their own
# page-type node (home via form.html, about.html), plus a programmatic zone page
# (zone_detail.html, owned by the parallel SEO task — read-only here).
PAGES = ["/it/", "/en/", "/it/about", "/en/about", "/it/zones/B12", "/en/zones/B12"]


class TestJsonLdBlocksAreValid:
    @pytest.mark.parametrize("path", PAGES)
    def test_every_block_is_valid_json_with_context_and_type(self, client: TestClient, path: str):
        body = client.get(path).text
        for block in _json_ld_blocks(body):
            assert block.get("@context") == "https://schema.org"
            assert block.get("@type")


class TestOrganizationPresence:
    @pytest.mark.parametrize("path", PAGES)
    def test_organization_node_present(self, client: TestClient, path: str):
        body = client.get(path).text
        org = _organization_node(_json_ld_blocks(body))
        assert org["name"] == "stimmo"
        assert org["url"] == "https://stimmo.it/"
        assert org["@id"] == "https://stimmo.it/#organization"

    @pytest.mark.parametrize(
        "path,other_type",
        [
            ("/it/", "WebApplication"),
            ("/it/about", "WebApplication"),
            ("/it/zones/B12", "Place"),
        ],
    )
    def test_pages_overriding_json_ld_still_carry_organization(
        self, client: TestClient, path: str, other_type: str
    ):
        """form.html, about.html, and zone_detail.html each override the
        overridable json_ld block with their own page-type node. The Organization
        node lives outside that block in base.html, so it must survive regardless."""
        blocks = _json_ld_blocks(client.get(path).text)
        types = {b["@type"] for b in blocks}
        assert other_type in types
        assert "Organization" in types

    def test_about_webapplication_node_references_organization(self, client: TestClient):
        """about.html is one of the two files this task owns, so its pre-existing
        WebApplication node gets linked to Organization via @id — one instance of
        the "reference rather than duplicate" pattern the task calls for. form.html
        (home) is left as-is: home already gets an Organization-linked WebSite node,
        so its floating WebApplication node is a smaller, out-of-scope loose end."""
        blocks = _json_ld_blocks(client.get("/it/about").text)
        webapp = next(b for b in blocks if b["@type"] == "WebApplication")
        org = _organization_node(blocks)
        assert webapp["publisher"] == {"@id": org["@id"]}


class TestSameAs:
    @pytest.mark.parametrize("path", PAGES)
    def test_same_as_only_lists_the_github_repo(self, client: TestClient, path: str):
        org = _organization_node(_json_ld_blocks(client.get(path).text))
        assert org["sameAs"] == ["https://github.com/alediaferia/stimmo"]


class TestLogo:
    @pytest.mark.parametrize("path", PAGES)
    def test_logo_url_is_absolute_https_and_file_exists(self, client: TestClient, path: str):
        org = _organization_node(_json_ld_blocks(client.get(path).text))
        logo = org["logo"]
        assert logo["@type"] == "ImageObject"
        logo_url = logo["url"]
        assert logo_url.startswith("https://stimmo.it/static/")

        # Strip the origin and the cache-busting `?v=` query string added by
        # static_url() to recover the real path under web/static/.
        rel_path = logo_url.removeprefix("https://stimmo.it/static/").split("?", 1)[0]
        assert (STATIC_DIR / rel_path).is_file(), (
            f"Organization.logo.url references {rel_path!r}, which doesn't exist under {STATIC_DIR}"
        )


class TestWebSite:
    @pytest.mark.parametrize("path", ["/it/", "/en/"])
    def test_website_node_on_home_references_organization(self, client: TestClient, path: str):
        blocks = _json_ld_blocks(client.get(path).text)
        sites = [b for b in blocks if b.get("@type") == "WebSite"]
        assert len(sites) == 1
        site = sites[0]
        assert site["name"] == "stimmo"
        assert site["url"] == "https://stimmo.it/"
        org = _organization_node(blocks)
        assert site["publisher"] == {"@id": org["@id"]}
        # No fabricated site-search endpoint.
        assert "potentialAction" not in site

    @pytest.mark.parametrize("path", ["/it/about", "/it/zones/B12"])
    def test_website_node_absent_off_home(self, client: TestClient, path: str):
        blocks = _json_ld_blocks(client.get(path).text)
        assert not any(b.get("@type") == "WebSite" for b in blocks)
