"""Tests for WP-8: neighborhood price pages layered on top of the OMI zone pages.

Covers /it/milano/{slug}-prezzi-al-mq and /en/milan/{slug}-property-prices for
every curated neighborhood, the 404 path for unknown slugs, per-language-slug
canonical/hreflang correctness, the sitemap's blurb-gated staggered launch, the
shared-zone methodological disclosure, and the up/out links added to the zone
pages (/{lang}/zones and /{lang}/zones/{code}).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from babel.numbers import format_decimal
from fastapi.testclient import TestClient

from stimmo.data import neighborhoods as nb_module
from stimmo.data import omi
from stimmo.web.app import _sitemap_xml, app

LANGS = ("it", "en")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def _eur(n: float) -> str:
    return format_decimal(round(n), format="#,##0", locale="it_IT")


def _url(n: nb_module.Neighborhood, lang: str) -> str:
    if lang == "it":
        return f"/it/milano/{n.slug_it}-prezzi-al-mq"
    return f"/en/milan/{n.slug_en}-property-prices"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    @pytest.mark.parametrize("lang", LANGS)
    @pytest.mark.parametrize("n", nb_module.list_neighborhoods(), ids=lambda n: n.slug_en)
    def test_every_neighborhood_renders_both_langs(
        self, client: TestClient, lang: str, n: nb_module.Neighborhood
    ):
        r = client.get(_url(n, lang))
        assert r.status_code == 200
        assert n.name in r.text

    @pytest.mark.parametrize("lang", LANGS)
    def test_unknown_slug_is_404(self, client: TestClient, lang: str):
        path = (
            "/it/milano/not-a-real-place-prezzi-al-mq"
            if lang == "it"
            else "/en/milan/not-a-real-place-property-prices"
        )
        r = client.get(path)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Canonical / hreflang
# ---------------------------------------------------------------------------


class TestCanonicalAndHreflang:
    def test_it_page_canonical_and_alternates(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("brera", "it")
        body = client.get(_url(n, "it")).text
        assert 'rel="canonical" href="http://testserver/it/milano/brera-prezzi-al-mq"' in body
        assert 'hreflang="it" href="http://testserver/it/milano/brera-prezzi-al-mq"' in body
        assert 'hreflang="en" href="http://testserver/en/milan/brera-property-prices"' in body

    def test_en_page_it_alternate_points_at_the_it_slug_not_the_english_one(
        self, client: TestClient
    ):
        # The exact regression this per-language-slug design exists to prevent:
        # the EN page's "it" alternate must point at the *-prezzi-al-mq URL, not
        # a same-shaped English one.
        n = nb_module.neighborhood_for_slug("brera", "en")
        body = client.get(_url(n, "en")).text
        assert 'rel="canonical" href="http://testserver/en/milan/brera-property-prices"' in body
        assert 'hreflang="en" href="http://testserver/en/milan/brera-property-prices"' in body
        assert 'hreflang="it" href="http://testserver/it/milano/brera-prezzi-al-mq"' in body
        assert "/en/milan/brera-prezzi-al-mq" not in body


# ---------------------------------------------------------------------------
# Sitemap: blurb-gated staggered launch
# ---------------------------------------------------------------------------


class TestSitemapGating:
    def test_no_neighborhood_urls_in_sitemap_yet(self, client: TestClient):
        # None of the curated neighborhoods carry editorial copy yet (that's a
        # separate pass) — so none should be in the sitemap, even though every
        # page already resolves and renders.
        body = client.get("/sitemap.xml").text
        assert "prezzi-al-mq" not in body
        assert "property-prices" not in body

    def test_gating_requires_both_language_blurbs(self, monkeypatch: pytest.MonkeyPatch):
        with_both = replace(
            nb_module.neighborhood_for_slug("brera", "it"),
            blurb_it="Testo editoriale sufficientemente lungo per Brera, quartiere storico.",
            blurb_en="Editorial copy long enough for Brera, a historic district.",
        )
        it_only = replace(
            nb_module.neighborhood_for_slug("isola", "it"),
            slug_it="isola-it-only",
            slug_en="isola-it-only-en",
            blurb_it="Solo italiano, manca la traduzione inglese.",
            blurb_en="",
        )
        en_only = replace(
            nb_module.neighborhood_for_slug("navigli", "it"),
            slug_it="navigli-en-only",
            slug_en="navigli-en-only-en",
            blurb_it="",
            blurb_en="Missing the Italian translation.",
        )
        monkeypatch.setattr(nb_module, "list_neighborhoods", lambda: [with_both, it_only, en_only])
        _sitemap_xml.cache_clear()
        try:
            body = _sitemap_xml()
        finally:
            _sitemap_xml.cache_clear()

        assert "brera-prezzi-al-mq" in body
        assert "brera-property-prices" in body
        # A neighborhood with only one language's blurb filled in must not appear
        # under *either* language — we don't want to index a thin page even in
        # the language that does have copy, since the sitemap entry always emits
        # both language URLs together (see the neighborhood_detail registration
        # in web/app.py). Checking both the it-only and en-only cases pins the
        # "AND", not just "en required" or "it required" alone.
        assert "isola-it-only" not in body
        assert "navigli-en-only" not in body


# ---------------------------------------------------------------------------
# Shared-zone methodological disclosure
# ---------------------------------------------------------------------------


class TestSharedZoneDisclosure:
    @pytest.mark.parametrize(
        "slug", ["isola", "porta-venezia", "porta-nuova", "navigli", "tortona-solari"]
    )
    def test_appears_for_shared_zone_neighborhoods(self, client: TestClient, slug: str):
        n = nb_module.neighborhood_for_slug(slug, "it")
        body = client.get(_url(n, "it")).text
        assert "No OMI zone of its own" in body

    def test_absent_for_brera(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("brera", "it")
        body = client.get(_url(n, "it")).text
        assert "No OMI zone of its own" not in body

    def test_isola_links_to_both_zone_sharers(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("isola", "it")
        body = client.get(_url(n, "it")).text
        assert 'href="/it/milano/porta-venezia-prezzi-al-mq"' in body
        assert 'href="/it/milano/porta-nuova-prezzi-al-mq"' in body

    def test_isola_disclosure_links_use_english_slugs_on_the_english_page(self, client: TestClient):
        # Cross-links are the actual SEO payload of this disclosure — pin that the
        # EN page links to the EN slugs, not the IT ones, mirroring the canonical/
        # hreflang correctness already required of the page itself.
        n = nb_module.neighborhood_for_slug("isola", "en")
        body = client.get(_url(n, "en")).text
        assert 'href="/en/milan/porta-venezia-property-prices"' in body
        assert 'href="/en/milan/porta-nuova-property-prices"' in body


# ---------------------------------------------------------------------------
# Headline number / multi-zone note
# ---------------------------------------------------------------------------


class TestHeadlineNumber:
    def test_single_zone_neighborhood_shows_its_omi_band(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("brera", "it")
        lo, hi = omi.zone_price_index()["B15"]
        body = client.get(_url(n, "it")).text
        assert _eur(lo) in body
        assert _eur(hi) in body

    def test_multi_zone_neighborhood_notes_the_span(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("duomo-centro-storico", "it")
        assert len(n.zone_codes) > 1
        body = client.get(_url(n, "it")).text
        assert "spans more than one OMI zone" in body

    def test_single_zone_neighborhood_has_no_span_note(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("brera", "it")
        body = client.get(_url(n, "it")).text
        assert "spans more than one OMI zone" not in body


# ---------------------------------------------------------------------------
# Up-links from zone pages, out-links from the zones index
# ---------------------------------------------------------------------------


class TestZoneDetailUpLinks:
    def test_single_neighborhood_zone_links_up(self, client: TestClient):
        body = client.get("/it/zones/B15").text
        assert 'href="/it/milano/brera-prezzi-al-mq"' in body

    def test_shared_zone_links_up_to_both_neighborhoods(self, client: TestClient):
        body = client.get("/it/zones/C12").text
        assert 'href="/it/milano/isola-prezzi-al-mq"' in body
        assert 'href="/it/milano/porta-venezia-prezzi-al-mq"' in body

    def test_zone_with_no_neighborhood_has_no_up_link(self, client: TestClient):
        assert nb_module.neighborhoods_for_zone("D10") == ()
        body = client.get("/it/zones/D10").text
        # Scoped to the "Part of" line itself, not a blanket "/it/milano/" absence —
        # neighborhood_url is a template global available page-wide, so a future
        # unrelated link using it elsewhere on the page shouldn't fail this test.
        assert "Part of" not in body

    def test_shared_zone_links_up_with_english_slugs_on_the_english_page(self, client: TestClient):
        body = client.get("/en/zones/C12").text
        assert 'href="/en/milan/isola-property-prices"' in body
        assert 'href="/en/milan/porta-venezia-property-prices"' in body


class TestZonesIndexOutLinks:
    def test_links_zone_to_its_neighborhood(self, client: TestClient):
        body = client.get("/it/zones").text
        # B15 (Brera) row must carry a link out to the neighborhood page.
        idx = body.find(">B15<")
        assert idx != -1
        window = body[idx : idx + 600]
        assert 'href="/it/milano/brera-prezzi-al-mq"' in window

    def test_en_index_links_to_english_slug(self, client: TestClient):
        body = client.get("/en/zones").text
        idx = body.find(">B15<")
        assert idx != -1
        window = body[idx : idx + 600]
        assert 'href="/en/milan/brera-property-prices"' in window
