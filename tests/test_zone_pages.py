"""Tests for WP-7: programmatic OMI zone pages (docs/distribution-plan.md, Phase C).

Covers /{lang}/zones (index) and /{lang}/zones/{code} (detail) in both langs,
the sitemap growing to include zone URLs, and the 404 path for unknown codes.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from stimmo.data import omi, zones
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


# ---------------------------------------------------------------------------
# R6 (2026-09-23 SEO review): differentiate /it/zones from "omi milano"
# incumbents (valorecasatua.it et al.) with a derived semester label, a
# computed quoted-zone count, and a "Cos'è l'OMI" explainer citing the primary
# source — rather than mirroring their copy. Scope: zones_index only, no
# zone_detail or neighbourhood-page changes. Acceptance tests per the brief's
# §6, run against the *rendered* page (never the .po msgstr directly).
# ---------------------------------------------------------------------------


def _title_of(body: str) -> str:
    return re.search(r"<title>(.*?)</title>", body, re.S).group(1)


def _h1_of(body: str) -> str:
    h1 = re.search(r"<h1>(.*?)</h1>", body, re.S).group(1)
    return re.sub(r"<[^>]+>", "", h1)


def _meta_description_of(body: str) -> str:
    descs = re.findall(r'<meta name="description" content="(.*?)">', body)
    assert len(descs) == 1
    return descs[0]


def _json_ld_name_of(body: str) -> str:
    # tojson-escapes non-ASCII (em dash, €, °) as \uXXXX, so extract the raw
    # JSON block and json.loads it rather than regex-matching the literal
    # bytes — a regex match would see escaped codepoints, not the real string.
    m = re.search(r'"@type": "CollectionPage".*?"name": (".*?")', body, re.S)
    assert m is not None
    return json.loads(m.group(1))


class TestZonesIndexSemesterLabel:
    """Acceptance test 1: the title carries the derived semester, in both
    languages — proving the label comes from data.omi.semester(), not a
    hard-coded string."""

    def test_semester_is_derived_not_hardcoded(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(omi, "semester", lambda: "2031-1")
        it_title = _title_of(client.get("/it/zones").text)
        en_title = _title_of(client.get("/en/zones").text)
        assert "1° sem. 2031" in it_title
        assert "H1 2031" in en_title


class TestZonesIndexNoPlaceholderLeakage:
    """Acceptance test 2: catches a translate_po.py output that mangled a
    %(var)s placeholder — none of the rendered surfaces should ever show a
    raw '%(' or an un-substituted '{{'."""

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_no_raw_placeholders_in_head_surfaces(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/zones").text
        for surface in (
            _title_of(body),
            _h1_of(body),
            _meta_description_of(body),
            _json_ld_name_of(body),
        ):
            assert "%(" not in surface
            assert "{{" not in surface


class TestZonesIndexTitleConsistency:
    """Acceptance test 4: title, og:title and JSON-LD name are the same
    string, the '— stimmo' / 'stimmo — ' affix aside — so they cannot drift
    apart the way the pre-R6 template's four separately-written copies could."""

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_title_og_title_and_json_ld_name_share_one_core_string(
        self, client: TestClient, lang: str
    ):
        body = client.get(f"/{lang}/zones").text
        title = _title_of(body)
        og_title = re.search(r'<meta property="og:title" content="(.*?)">', body).group(1)
        json_ld_name = _json_ld_name_of(body)

        assert title.endswith(" — stimmo")
        core = title[: -len(" — stimmo")]
        assert og_title == f"stimmo — {core}"
        assert json_ld_name == core


class TestZonesIndexQuotedZoneCount:
    """Acceptance test 5: the quoted-zone count (how many of the 43 zones
    actually carry a bundled OMI band) is computed from omi.zone_price_index(),
    never typed into a template or msgid."""

    def test_count_is_computed_from_the_price_index(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        real_index = omi.zone_price_index()
        one_fewer = dict(real_index)
        dropped_code = next(iter(one_fewer))
        del one_fewer[dropped_code]
        monkeypatch.setattr(omi, "zone_price_index", lambda: one_fewer)

        body = client.get("/it/zones").text
        expected_quoted = len(one_fewer)
        expected_total = len(zones.list_zones())
        # Rendered in the .meta corner as "{{ quoted }} / {{ total }}"...
        assert f"{expected_quoted} / {expected_total}" in body
        # ...and again inside the explainer's zones-grouped-by-fascia paragraph.
        assert str(expected_quoted) in body
        assert str(expected_total) in body

    def test_real_data_shows_42_of_43(self, client: TestClient):
        body = client.get("/it/zones").text
        assert "42 / 43" in body


class TestZonesIndexExplainer:
    """Acceptance test 6: the "Cos'è l'OMI" explainer is present in both
    languages, anchored at #cose-omi, cites both primary-source links exactly,
    and links back to the estimator."""

    _AGENZIA_ENTRATE_URL = (
        "https://www.agenziaentrate.gov.it/portale/schede/fabbricatiterreni/"
        "omi/banche-dati/quotazioni-immobiliari"
    )
    _COMUNE_MILANO_URL = "https://dati.comune.milano.it/dataset?tags=OMI"

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_explainer_anchor_and_sources_present(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/zones").text
        assert 'id="cose-omi"' in body
        assert self._AGENZIA_ENTRATE_URL in body
        assert self._COMUNE_MILANO_URL in body
        assert f'href="/{lang}/"' in body


class TestZonesIndexMetaDescriptionLength:
    """Acceptance test 7: the meta description stays within Google's ~160
    character display budget, as actually rendered (semester + count filled
    in), in both languages."""

    @pytest.mark.parametrize("lang", ["it", "en"])
    def test_meta_description_at_most_160_chars(self, client: TestClient, lang: str):
        body = client.get(f"/{lang}/zones").text
        assert len(_meta_description_of(body)) <= 160


class TestZonesIndexItalianIsReallyItalian:
    """Acceptance test 8: catches an untranslated fallback — the Italian
    explainer heading must not be the bare English msgid. Requires
    scripts/translate_po.py to have produced a real Italian msgstr; a
    passing pybabel compile with a fuzzy/missing entry silently falls back
    to English, which this test exists to catch."""

    def test_it_explainer_heading_is_not_the_english_msgid(self, client: TestClient):
        body = client.get("/it/zones").text
        heading = re.search(
            r'<h2 class="doc-title">(.*?)</h2>', body[body.find("cose-omi") :]
        ).group(1)
        assert heading.strip() != "What OMI is, and how to read it"


class TestZonesIndexLastmodCouplesWithR4:
    """Acceptance test 9: zones_index's <lastmod> is later than the OMI
    semester start (its own R6 copy-change date, per _zones_index_lastmod),
    while a sibling zone_detail page's <lastmod> still equals the plain
    semester start — proving the two suppliers didn't get merged into one."""

    def test_zones_index_lastmod_newer_than_semester_zone_detail_is_not(self, client: TestClient):
        from stimmo.web.app import _semester_start_date

        body = client.get("/sitemap.xml").text
        semester_date = _semester_start_date(omi.semester())

        idx = body.find("<loc>https://stimmo.it/it/zones</loc>")
        zones_index_lastmod = re.search(r"<lastmod>([^<]+)</lastmod>", body[idx:]).group(1)
        assert zones_index_lastmod > semester_date.isoformat()

        code, _descr = zones.list_zones()[0]
        idx2 = body.find(f"<loc>https://stimmo.it/it/zones/{code}</loc>")
        zone_detail_lastmod = re.search(r"<lastmod>([^<]+)</lastmod>", body[idx2:]).group(1)
        assert zone_detail_lastmod == semester_date.isoformat()


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
