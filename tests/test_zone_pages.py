"""Tests for WP-7: programmatic OMI zone pages (docs/distribution-plan.md, Phase C).

Covers /{lang}/zones (index) and /{lang}/zones/{code} (detail) in both langs,
the sitemap growing to include zone URLs, and the 404 path for unknown codes.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from stimmo.data import zones
from stimmo.web.app import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


SAMPLE_ZONE_CODES = ["B12", "D10", "R2"]  # R2 has no bundled Compr_min/max rows


class TestZonesIndex:
    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_renders_200(self, client: TestClient, lang: str):
        r = client.get(f"/{lang}/zones")
        assert r.status_code == 200

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_lists_zone_codes_grouped(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/zones").text
        assert "B12" in body
        assert f'href="/{lang}/zones/B12"' in body

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_canonical_and_hreflang(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/zones").text
        assert f'rel="canonical" href="http://testserver/{lang}/zones"' in body
        assert 'hreflang="it"' in body
        assert 'hreflang="en"' in body
        assert 'hreflang="x-default"' in body

    # R1 (2026-08-30 SEO review, §4): /it/zones was titled for low-volume long-tail
    # queries ("zone omi milano", "fasce omi milano") while sitting at position 17.2
    # for "omi milano" — 35 impressions, a third of the whole topical bucket, 0%
    # CTR. Retargeted the msgid so its Italian translation carries "omi milano" (the
    # head term) — asserted here against the *rendered, translated* page, per the
    # report's own acceptance bar: "the acceptance test is the rendered Italian
    # <title>, not the pybabel run completing." Never assert this against the .po
    # msgstr directly — a passing pybabel compile proves nothing about what a
    # crawler actually sees.
    def test_it_title_targets_omi_milano_head_term(self, client: TestClient):
        body = client.get("/it/zones").text
        title = re.search(r"<title>(.*?)</title>", body, re.S).group(1)
        assert "omi milano" in title.lower()

    def test_it_h1_targets_omi_milano_head_term(self, client: TestClient):
        body = client.get("/it/zones").text
        h1 = re.search(r"<h1>(.*?)</h1>", body, re.S).group(1)
        assert "omi milano" in re.sub(r"<[^>]+>", "", h1).lower()

    def test_it_meta_description_targets_omi_milano_head_term(self, client: TestClient):
        body = client.get("/it/zones").text
        descs = re.findall(r'<meta name="description" content="(.*?)">', body)
        assert len(descs) == 1
        assert "omi milano" in descs[0].lower()

    def test_it_json_ld_name_targets_omi_milano_head_term(self, client: TestClient):
        body = client.get("/it/zones").text
        m = re.search(r'"@type": "CollectionPage".*?"name": "(.*?)"', body, re.S)
        assert m is not None
        assert "omi milano" in m.group(1).lower()

    # Stays explicitly OMI-technical (per the report's constraint) rather than
    # drifting toward the neighbourhood hub pages' colloquial "prezzi al mq"
    # vocabulary, which targets a deliberately different query family.
    def test_it_title_does_not_use_hub_page_vocabulary(self, client: TestClient):
        body = client.get("/it/zones").text
        title = re.search(r"<title>(.*?)</title>", body, re.S).group(1)
        assert "prezzi al mq" not in title.lower()


class TestZoneDetail:
    @pytest.mark.parametrize("lang", ["it", "en"])
    @pytest.mark.parametrize("code", SAMPLE_ZONE_CODES)
    def test_renders_200(self, client: TestClient, lang: str, code: str):
        r = client.get(f"/{lang}/zones/{code}")
        assert r.status_code == 200
        assert code in r.text

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_canonical_and_hreflang(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/zones/B12").text
        assert f'rel="canonical" href="http://testserver/{lang}/zones/B12"' in body
        assert 'hreflang="it"' in body
        assert 'hreflang="en"' in body

    def test_unknown_zone_code_is_404(self, client: TestClient):
        r = client.get("/it/zones/NOPE")
        assert r.status_code == 404

    def test_zone_with_no_bundled_quotes_still_renders(self, client: TestClient):
        # R2 has a polygon but no Compr_min/max rows — must degrade gracefully,
        # not 500.
        r = client.get("/it/zones/R2")
        assert r.status_code == 200

    def test_trend_panel_present_for_zone_with_history(self, client: TestClient):
        body = client.get("/it/zones/B12").text
        assert "strip-axis" in body

    def test_map_container_present(self, client: TestClient):
        body = client.get("/it/zones/B12").text
        assert "zone-detail-map" in body

    def test_back_link_to_zones_index(self, client: TestClient):
        body = client.get("/it/zones/B12").text
        assert 'href="/it/zones"' in body


class TestZonesInSitemap:
    def test_zone_index_in_sitemap(self, client: TestClient):
        body = client.get("/sitemap.xml").text
        assert "https://stimmo.it/it/zones</loc>" in body
        assert "https://stimmo.it/en/zones</loc>" in body

    def test_all_zone_detail_urls_in_sitemap_both_langs(self, client: TestClient):
        body = client.get("/sitemap.xml").text
        for code, _descr in zones.list_zones():
            assert f"https://stimmo.it/it/zones/{code}</loc>" in body
            assert f"https://stimmo.it/en/zones/{code}</loc>" in body


class TestFooterAndAboutLinks:
    def test_footer_links_to_zones_index(self, client: TestClient):
        body = client.get("/it/").text
        assert 'href="/it/zones"' in body

    def test_about_page_links_to_zones_index(self, client: TestClient):
        body = client.get("/it/about").text
        assert 'href="/it/zones"' in body
