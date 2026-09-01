"""Tests for WP-8: neighborhood price pages layered on top of the OMI zone pages.

Covers /it/milano/{slug}-prezzi-al-mq and /en/milan/{slug}-property-prices for
every curated neighborhood, the 404 path for unknown slugs, per-language-slug
canonical/hreflang correctness, the sitemap's blurb-gated staggered launch, the
shared-zone methodological disclosure, and the up/out links added to the zone
pages (/{lang}/zones and /{lang}/zones/{code}).
"""

from __future__ import annotations

import json
import re
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


@pytest.fixture()
def content_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Points STIMMO_CONTENT_DIR at an empty tmp_path and returns a writer that
    drops a neighborhoods.json there.

    Injecting real content through the env var + loader (rather than monkeypatching
    `list_neighborhoods` directly, as `test_gating_requires_both_language_blurbs`
    above does) proves the loader itself works end to end, not just the gating
    boolean downstream of it. Both `neighborhoods._neighborhoods_with_content` (the
    loader's own cache) and `_sitemap_xml` (which calls `list_neighborhoods()`
    while building the sitemap) are cleared on every write and on teardown, the
    same two-cache pattern `test_seo.seo_registry_sandbox` uses for `_sitemap_xml`
    alone — otherwise a stale sitemap or stale blurbs could leak into another test.
    """

    def _write(mapping: dict) -> None:
        (tmp_path / "neighborhoods.json").write_text(json.dumps(mapping), encoding="utf-8")
        nb_module._neighborhoods_with_content.cache_clear()
        _sitemap_xml.cache_clear()

    monkeypatch.setenv("STIMMO_CONTENT_DIR", str(tmp_path))
    nb_module._neighborhoods_with_content.cache_clear()
    _sitemap_xml.cache_clear()
    yield _write
    nb_module._neighborhoods_with_content.cache_clear()
    _sitemap_xml.cache_clear()


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

    def test_exactly_one_meta_description(self, client: TestClient):
        # Regression guard for the base.html bug found while implementing R1 (see
        # test_seo.py's test_exactly_one_meta_description_per_page): this template
        # sets its own og_meta-block description (_descr), which used to render
        # *alongside* base.html's site-wide default rather than instead of it.
        n = nb_module.neighborhood_for_slug("brera", "it")
        body = client.get(_url(n, "it")).text
        assert len(re.findall(r'<meta name="description"', body)) == 1


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
    def test_neighborhood_appears_in_sitemap_iff_both_blurbs_present(
        self, client: TestClient, content_dir
    ):
        # The rule, not a snapshot of who currently has copy: a neighborhood enters
        # the sitemap once (and only once) BOTH blurb_it and blurb_en are non-empty.
        # Driven through the real content file (not a monkeypatched table), so this
        # also proves data.neighborhoods' loader wires up correctly end to end —
        # unlike test_gating_requires_both_language_blurbs below, which pins the
        # gating boolean itself via a monkeypatched list_neighborhoods().
        brera = nb_module.neighborhood_for_slug("brera", "it")
        isola = nb_module.neighborhood_for_slug("isola", "it")
        navigli = nb_module.neighborhood_for_slug("navigli", "it")

        content_dir(
            {
                brera.slug_en: {
                    "blurb_it": "Testo editoriale sufficientemente lungo per Brera.",
                    "blurb_en": "Editorial copy long enough for Brera.",
                },
                isola.slug_en: {
                    "blurb_it": "Solo italiano, manca la traduzione inglese.",
                    "blurb_en": "",
                },
                navigli.slug_en: {
                    "blurb_it": "",
                    "blurb_en": "Missing the Italian translation.",
                },
                # bicocca has no entry at all — the common case for most of the
                # table at any given time, and it must degrade the same way as a
                # missing content file: no blurb, no sitemap entry.
            }
        )

        body = client.get("/sitemap.xml").text

        assert "brera-prezzi-al-mq" in body
        assert "brera-property-prices" in body
        # A neighborhood with only one language's blurb filled in must not appear
        # under *either* language — the sitemap entry always emits both language
        # URLs as a pair (see the neighborhood_detail registration in web/app.py),
        # so a thin page in the language that does have copy still isn't indexed.
        assert "isola-prezzi-al-mq" not in body
        assert "isola-property-prices" not in body
        assert "navigli-prezzi-al-mq" not in body
        assert "navigli-property-prices" not in body
        assert "bicocca-prezzi-al-mq" not in body
        assert "bicocca-property-prices" not in body

        # And the page itself actually renders the injected copy — the point of
        # going through the real loader instead of a monkeypatched table.
        detail_body = client.get(_url(brera, "it")).text
        assert "Testo editoriale sufficientemente lungo per Brera." in detail_body

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
        assert 'data-testid="shared-zone-disclosure"' in body

    def test_absent_for_brera(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("brera", "it")
        body = client.get(_url(n, "it")).text
        assert 'data-testid="shared-zone-disclosure"' not in body

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
        assert 'data-testid="multi-zone-span-note"' in body

    def test_single_zone_neighborhood_has_no_span_note(self, client: TestClient):
        n = nb_module.neighborhood_for_slug("brera", "it")
        body = client.get(_url(n, "it")).text
        assert 'data-testid="multi-zone-span-note"' not in body


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


# ---------------------------------------------------------------------------
# Startup validation: a malformed content file must abort the deploy, not 500
# every neighborhood page + the sitemap while the process looks healthy.
# ---------------------------------------------------------------------------
class TestStartupValidation:
    def test_malformed_content_file_fails_app_startup(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """web/app.py's `_lifespan` forces the content file to load before the app
        accepts traffic. Entering TestClient as a context manager (`with
        TestClient(app):`) is what actually drives FastAPI/Starlette's lifespan
        startup — constructing it bare does not — so this is the one way to
        exercise "does the app fail to start", as opposed to "does the first
        request fail" (the old, per-request-lazy behavior this replaces).
        """
        (tmp_path / "neighborhoods.json").write_text("{not valid json", encoding="utf-8")
        monkeypatch.setenv("STIMMO_CONTENT_DIR", str(tmp_path))
        nb_module._neighborhoods_with_content.cache_clear()
        # NOTE: the MCP session manager wired into `_lifespan` (see web/app.py) is a
        # module-level singleton whose `.run()` may only be entered once per process
        # (mcp.server.streamable_http_manager.StreamableHTTPSessionManager raises
        # RuntimeError on a second call) — so this suite gets exactly one test that
        # drives FastAPI/Starlette lifespan startup via `with TestClient(app):`.
        # Absent-file-starts-up-cleanly is already covered without the lifespan by
        # the plain `client` fixture used throughout this file and by test_seo.py's
        # empty_content_dir fixture.
        try:
            with pytest.raises(ValueError, match="malformed neighborhood content file"):
                with TestClient(app):
                    pass
        finally:
            nb_module._neighborhoods_with_content.cache_clear()
