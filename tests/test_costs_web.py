"""Web surface of the purchase-cost calculator and the interest button: the standalone page,
the result-page section, the SEO registry entry, the env-gated button and its counters."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stimmo.costs import rates
from stimmo.data import amenities, geocode
from stimmo.i18n import gettext, use_locale
from stimmo.models import (
    AmenityScore,
    CadastralCategory,
    PurchaseScenario,
    RenovationTier,
    SellerType,
)
from stimmo.web import app as app_module
from stimmo.web import costs_view, labels
from stimmo.web.app import _sitemap_xml, app, application
from stimmo.web.metrics import DOSSIER_INTEREST, REQUESTS
from stimmo.web.share_store import SqliteShareStore


def _italian_missing(msgid: str) -> bool:
    with use_locale("it_IT"):
        return gettext(msgid) == msgid


def needs_italian(msgid: str):
    """Skip an assertion about Italian *wording* until the catalog has been filled in by
    scripts/translate_po.py (msgstr is never hand-written). Formatting assertions don't need it."""
    return pytest.mark.skipif(
        _italian_missing(msgid),
        reason=f"it_IT has no translation for {msgid!r} yet: run scripts/translate_po.py",
    )


IT_URL = "/it/costi-acquisto-casa-milano"
EN_URL = "/en/milan/home-buying-costs"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def estimate_env(monkeypatch: pytest.MonkeyPatch):
    """Nominatim/Overpass stubbed and a throwaway share store, so POST /estimate is hermetic."""
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())
    monkeypatch.setattr(app_module, "_share_store", SqliteShareStore(":memory:"))


def _form(**kw) -> dict[str, str]:
    data = {
        "address": "Piazza Duomo, Milano",
        "surface_m2": "70",
        "property_type": "Abitazioni civili",
        "fine_condition": "abitabile",
        "floor": "2",
        "total_floors": "5",
        "has_lift": "on",
        "energy_class": "",
        "outdoor": "none",
        "has_box": "off",
        "construction_era": "postwar_boom",
        "orientation": "mixed",
        "has_second_bathroom": "off",
        "asking_price_eur": "450000",
    }
    data.update(kw)
    return data


def _counter(event: str, lang: str) -> float:
    return DOSSIER_INTEREST.labels(event=event, lang=lang)._value.get()


def _selected(body: str, select_id: str) -> str:
    block = re.search(rf'<select[^>]*id="{select_id}".*?</select>', body, re.S)
    assert block, select_id
    chosen = re.search(r'<option value="([^"]*)"\s+selected', block.group(0))
    assert chosen, select_id
    return chosen.group(1)


def _total(body: str) -> str:
    m = re.search(
        r'<div class="lbl">Total on top of the price</div>\s*<div class="val mono">([^<]+)</div>',
        body,
    )
    assert m
    return m.group(1)


# ---------------------------------------------------------------------------
# Standalone calculator page + SEO registry
# ---------------------------------------------------------------------------


class TestCalculatorPage:
    @pytest.mark.parametrize("path", [IT_URL, EN_URL])
    def test_renders(self, client: TestClient, path: str):
        r = client.get(path)
        assert r.status_code == 200
        assert 'id="costs-result"' in r.text
        assert 'id="method"' in r.text

    def test_head_is_supported(self, client: TestClient):
        assert client.head(EN_URL).status_code == 200

    def test_is_registered_through_the_seo_registry(self):
        route = app_module._SEO_ROUTES["purchase_costs"]
        assert route.endpoint == "purchase_costs"
        assert dict(route.suffixes) == {
            "it": "/costi-acquisto-casa-milano",
            "en": "/milan/home-buying-costs",
        }

    def test_registry_endpoint_exists_as_a_real_html_route(self):
        from fastapi.responses import HTMLResponse
        from fastapi.routing import APIRoute

        html = {
            r.endpoint.__name__
            for r in app.routes
            if isinstance(r, APIRoute) and r.response_class is HTMLResponse
        }
        assert "purchase_costs" in html
        # The interest POST is a non-HTML endpoint: it must not need an SEO decision.
        assert "dossier_interest" not in html

    @pytest.mark.parametrize(("path", "lang"), [(IT_URL, "it"), (EN_URL, "en")])
    def test_canonical_and_hreflang(self, client: TestClient, path: str, lang: str):
        body = client.get(path).text
        assert f'<link rel="canonical" href="http://testserver{path}">' in body
        assert f'hreflang="it" href="http://testserver{IT_URL}"' in body
        assert f'hreflang="en" href="http://testserver{EN_URL}"' in body
        assert f'hreflang="x-default" href="http://testserver{IT_URL}"' in body

    def test_in_sitemap_with_both_languages_and_a_rates_lastmod(self, client: TestClient):
        _sitemap_xml.cache_clear()
        body = client.get("/sitemap.xml").text
        assert f"https://stimmo.it{IT_URL}</loc>" in body
        assert f"https://stimmo.it{EN_URL}</loc>" in body
        block = body[body.index(f"https://stimmo.it{IT_URL}</loc>") :].split("</url>")[0]
        assert f"<lastmod>{rates.RATES_AS_OF.isoformat()}</lastmod>" in block

    @pytest.mark.parametrize(
        "path", ["/en/costi-acquisto-casa-milano", "/it/milan/home-buying-costs"]
    )
    def test_other_languages_slug_does_not_exist(self, client: TestClient, path: str):
        assert client.get(path).status_code == 404

    def test_single_meta_description_and_json_ld(self, client: TestClient):
        body = client.get(EN_URL).text
        assert len(re.findall(r'<meta name="description"', body)) == 1
        assert '"@type": "WebApplication"' in body

    def test_bare_url_is_the_indexable_worked_example(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "noindex" not in body
        assert "Worked example" in body
        # EUR 450,000 / rendita 1,100 / EUR 300,000 mortgage / medium renovation.
        for figure in ("2,541", "16,470", "€750"):
            assert figure in body

    def test_bare_url_prefills_the_worked_example_inputs(self, client: TestClient):
        body = client.get(EN_URL).text
        assert re.search(r'id="cost-price" name="price"[^>]*value="450000"', body, re.S)
        assert re.search(r'id="cost-rendita" name="rendita"[^>]*value="1100"', body, re.S)
        assert re.search(r'id="cost-mortgage" name="mortgage"[^>]*value="300000"', body, re.S)
        assert _selected(body, "cost-tier") == "medium"
        assert _selected(body, "cost-category") == "A/2"

    def test_echoed_input_is_html_escaped(self, client: TestClient):
        body = client.get(f'{EN_URL}?price=450000&rendita="><script>alert(1)</script>').text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body

    def test_submitted_values_are_echoed_back_even_when_invalid(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=450000&mortgage=999999&rendita=1234").text
        assert re.search(r'id="cost-mortgage" name="mortgage"[^>]*value="999999"', body, re.S)
        assert re.search(r'id="cost-rendita" name="rendita"[^>]*value="1234"', body, re.S)

    def test_italian_page_uses_italian_number_format(self, client: TestClient):
        body = client.get(IT_URL).text
        assert "€ 2.541" in body
        assert "€ 16.470" in body

    def test_submitted_scenario_is_noindex_with_a_bare_canonical(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=300000&home=other&surface=50&tier=light").text
        assert '<meta name="robots" content="noindex, follow">' in body
        assert f'<link rel="canonical" href="http://testserver{EN_URL}">' in body
        assert "Your estimate" in body

    def test_form_is_a_plain_get_form_that_roundtrips(self, client: TestClient):
        body = client.get(EN_URL).text
        form = re.search(r'<form method="get" action="([^"]+)" class="costs-form">', body)
        assert form and form.group(1) == f"{EN_URL}#costs-result"
        expected = client.get(
            EN_URL + "?price=450000&surface=70&home=prima&seller=private&category=A/2&rendita=1100"
            "&agency=3&mortgage=300000&tier=medium"
        ).text
        assert _total(expected) == _total(body)

    def test_missing_rendita_is_shown_as_a_bounded_range_with_a_prompt(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=450000&home=prima&seller=private").text
        assert "depends on the rendita catastale" in body
        assert "needs your input" in body
        assert "€1,000 – €9,000" in body
        assert "The registro tax depends on the rendita catastale" in body

    def test_builder_seller_path(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=450000&home=prima&seller=builder_vat").text
        assert "IVA on the price (4%)" in body
        assert "€18,000" in body

    def test_excluded_category_note(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=450000&home=prima&category=A/1&rendita=1100").text
        assert "excluded from the prima-casa regime" in body

    @pytest.mark.parametrize(
        "query",
        [
            "price=abc",
            "price=5",
            "price=450000&mortgage=500000",
            "price=450000&tier=full",  # renovation without a surface
            "price=450000&seller=martian",
            "price=450000&agency=40",
        ],
    )
    def test_invalid_input_shows_errors_and_no_result(self, client: TestClient, query: str):
        r = client.get(f"{EN_URL}?{query}")
        assert r.status_code == 200
        assert 'class="note error"' in r.text
        assert 'id="costs-result"' not in r.text

    def test_methodology_cites_the_sources_and_the_rates(self, client: TestClient):
        body = client.get(EN_URL).text
        assert rates.AGENZIA_ENTRATE_GUIDE_URL in body
        assert rates.RENOVATION_SOURCE_URL in body
        assert rates.BONUS_RISTRUTTURAZIONE[2027].source_url in body
        assert "subject to the 2027 budget law" in body
        assert "never subtracted from the totals" in body
        assert "larger extraordinary-maintenance intervention" in body

    def test_provisional_bonus_year_is_labelled(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "subject to the 2027 budget law" in body

    def test_indicative_figures_are_labelled(self, client: TestClient):
        body = client.get(EN_URL).text
        assert 'class="tag indicative">indicative' in body


# ---------------------------------------------------------------------------
# Result-page section
# ---------------------------------------------------------------------------


class TestMortgageLtvPage:
    """The mortgage field is one number plus a unit select; it works without JavaScript."""

    BASE = "price=450000&home=prima&rendita=1100&agency=3"

    def test_field_is_a_number_and_a_unit_select_in_a_plain_get_form(self, client: TestClient):
        body = client.get(EN_URL).text
        start = body.index('<form method="get"')
        form = body[start : body.index("</form>", start)]
        assert re.search(r'id="cost-mortgage" name="mortgage"[^>]*type="number"', form, re.S)
        select = re.search(r'<select[^>]*name="mortgage_unit".*?</select>', form, re.S)
        assert select
        assert re.findall(r'<option value="([^"]+)"', select.group(0)) == ["eur", "pct"]
        assert "<script" not in form and "onchange" not in form and "onclick" not in form
        assert _selected(body, "cost-mortgage-unit") == "eur"  # the worked example is in euros

    def test_the_form_says_the_percentage_is_computed_on_the_price(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "A percentage is computed on the price" in body
        assert "appraisal (perizia)" in body
        assert "so it is an approximation" in body

    def test_percentage_round_trips_through_the_query_string(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=80&mortgage_unit=pct").text
        assert re.search(r'id="cost-mortgage" name="mortgage"[^>]*value="80"', body, re.S)
        assert _selected(body, "cost-mortgage-unit") == "pct"
        assert "mortgage €360,000 (80% of the price)" in body
        assert 'id="costs-result"' in body

    def test_euros_round_trip_through_the_query_string(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=360000&mortgage_unit=eur").text
        assert re.search(r'id="cost-mortgage" name="mortgage"[^>]*value="360000"', body, re.S)
        assert _selected(body, "cost-mortgage-unit") == "eur"
        assert "mortgage €360,000 (80% of the price)" in body

    def test_a_link_without_a_unit_still_means_euros(self, client: TestClient):
        # Links built before the unit existed carry only `mortgage=<euros>`.
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=300000").text
        assert _selected(body, "cost-mortgage-unit") == "eur"
        assert "mortgage €300,000" in body

    def test_amount_and_percentage_pages_show_the_same_totals(self, client: TestClient):
        by_pct = client.get(f"{EN_URL}?{self.BASE}&mortgage=80&mortgage_unit=pct").text
        by_eur = client.get(f"{EN_URL}?{self.BASE}&mortgage=360000&mortgage_unit=eur").text
        assert _total(by_pct) == _total(by_eur)
        assert "€900" in by_pct  # imposta sostitutiva 0.25% of 360,000

    def test_cash_needed_is_split_into_down_payment_and_costs(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=80&mortgage_unit=pct").text
        assert "Cash needed up front" in body
        assert re.search(r"€90,000 down payment \+ €[\d,]+ costs", body)

    @pytest.mark.parametrize("value", ["100.5", "101", "5000", "300000"])
    def test_a_percentage_above_100_shows_an_error_and_no_result(
        self, client: TestClient, value: str
    ):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage={value}&mortgage_unit=pct").text
        assert "The mortgage percentage must be between 0% and 100%." in body
        assert 'id="costs-result"' not in body
        assert re.search(rf'id="cost-mortgage" name="mortgage"[^>]*value="{value}"', body, re.S)
        assert _selected(body, "cost-mortgage-unit") == "pct"

    def test_unknown_unit_shows_an_error(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=80&mortgage_unit=usd").text
        assert 'class="note error"' in body and 'id="costs-result"' not in body

    def test_ltv_page_is_noindex_like_any_submitted_scenario(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=80&mortgage_unit=pct").text
        assert '<meta name="robots" content="noindex, follow">' in body

    # --- notes on the page ------------------------------------------------------------------

    def test_above_80_percent_the_limit_note_is_shown_with_its_source(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=90&mortgage_unit=pct").text
        assert "the ordinary limit is 80% of the value of the mortgaged property" in body
        assert "up to 100% only with integrative guarantees" in body
        assert rates.BANCA_ITALIA_CIRC_229_URL in body

    def test_at_80_percent_there_is_no_limit_note(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=80&mortgage_unit=pct").text
        assert "the ordinary limit is 80%" not in body.split('id="method"')[0]

    def test_consap_note_only_when_its_conditions_hold(self, client: TestClient):
        eligible = f"{EN_URL}?price=290000&home=prima&rendita=1100&mortgage=85&mortgage_unit=pct"
        body = client.get(eligible).text
        results = body[body.index('id="costs-result"') : body.index('id="method"')]
        assert "Fondo di garanzia per la prima casa" in results
        assert rates.CONSAP_FUND_URL in results
        for query in (
            "price=290000&home=other&rendita=1100&mortgage=85&mortgage_unit=pct",  # not prima casa
            "price=290000&home=prima&category=A/9&rendita=1100&mortgage=85&mortgage_unit=pct",
            "price=450000&home=prima&rendita=1100&mortgage=85&mortgage_unit=pct",  # loan > 250k
            "price=290000&home=prima&rendita=1100&mortgage=80&mortgage_unit=pct",  # not above 80%
        ):
            page = client.get(f"{EN_URL}?{query}").text
            results = page[page.index('id="costs-result"') : page.index('id="method"')]
            assert "Fondo di garanzia per la prima casa" not in results, query

    def test_full_financing_note_at_100_percent(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=100&mortgage_unit=pct").text
        assert "A loan equal to the whole price does not cover the purchase taxes" in body

    def test_the_price_caveat_note_is_shown_with_any_mortgage(self, client: TestClient):
        for query in ("mortgage=20&mortgage_unit=pct", "mortgage=300000&mortgage_unit=eur"):
            body = client.get(f"{EN_URL}?{self.BASE}&{query}").text
            assert "The loan-to-value ratio (" in body and "is computed on the price" in body

    def test_no_mortgage_no_ltv_notes(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=").text
        assert "loan-to-value ratio (" not in body
        assert "Cash needed up front" not in body

    def test_the_bare_worked_example_gets_the_price_caveat(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "The loan-to-value ratio (66.67%) is computed on the price" in body

    # --- methodology -----------------------------------------------------------------------

    def test_methodology_explains_the_limits_and_cites_the_primary_texts(self, client: TestClient):
        body = client.get(EN_URL).text
        assert 'id="ltv"' in body
        assert "Mortgage — loan-to-value limits" in body
        assert "caps it at 80% of the value of the mortgaged property" in body
        assert "raised up to 100% when integrative guarantees are provided" in body
        assert "plus the guarantees" in body
        for url in (rates.TUB_ART_38_URL, rates.BANCA_ITALIA_CIRC_229_URL, rates.CONSAP_FUND_URL):
            assert url in body
        assert "not an assessment of any buyer" in body

    def test_methodology_numbers_come_from_rates(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(rates, "MUTUO_FONDIARIO_MAX_LTV_PCT", 75.0)
        monkeypatch.setattr(rates, "CONSAP_MAX_LOAN_EUR", 123_000.0)
        body = client.get(EN_URL).text
        assert "caps it at 75% of the value" in body
        assert "loans of up to €123,000" in body

    def test_sources_list_the_new_texts(self):
        urls = {url for _, url in costs_view.SOURCES}
        cited = {
            rates.TUB_ART_38_URL,
            rates.BANCA_ITALIA_CIRC_229_URL,
            rates.CONSAP_FUND_URL,
            rates.CONSAP_DL73_ART64_URL,
            rates.CONSAP_DL66_ART4BIS_URL,
        }
        assert cited <= urls

    def test_methodology_describes_the_consap_fund_as_the_official_page_does(
        self, client: TestClient
    ):
        body = client.get(EN_URL).text
        method = body[body.index('id="ltv"') : body.index('id="payment"')]
        assert "Access is reserved to priority categories" in method
        # The large families' ISEE figures are access limits by number of children.
        assert (
            "families with three, four or five or more children under 21 and an ISEE of at most "
            "€40,000, €45,000 or €50,000 respectively"
        ) in method
        assert "From August 3, 2026 the list also includes people with a permanent" in method
        assert "law 104/1992" in method
        # Two separate rules for the higher guarantee, the second on the price with accessory costs.
        assert "under two separate rules" in method
        assert "whatever the size of the loan relative to the price" in method
        assert "above 80% of the purchase price including accessory costs" in method
        assert "for applications up to December 31, 2027" in method
        assert "for large families)" not in method
        # Applicant and property conditions.
        assert "must not own another home, including abroad" in method
        assert "luxury features of ministerial decree no. 1072 of 2 August 1969" in method
        assert "not applied here" in method

    def test_methodology_consap_figures_come_from_rates(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(rates, "CONSAP_DISABILITY_ENHANCED_ISEE_EUR", 41_000.0)
        monkeypatch.setattr(rates, "CONSAP_ENHANCED_ISEE_EUR", 42_000.0)
        body = client.get(EN_URL).text
        method = body[body.index('id="ltv"') : body.index('id="payment"')]
        assert "when the ISEE is at most €41,000" in method
        assert "with an ISEE of at most €42,000" in method

    # --- report an error ---------------------------------------------------------------------

    ISSUE_FORM_URL = (
        "https://github.com/alediaferia/stimmo/issues/new?template=correzione-contenuti.yml"
    )

    @pytest.mark.parametrize("path", [IT_URL, EN_URL])
    def test_the_page_links_to_the_content_correction_form_near_the_sources(
        self, client: TestClient, path: str
    ):
        body = client.get(path).text
        assert costs_view.REPORT_ISSUE_URL == self.ISSUE_FORM_URL
        sources = body[body.index('class="costs-sources"') :]
        assert f'href="{self.ISSUE_FORM_URL}"' in sources[: sources.index("</details>")]

    def test_the_english_note_invites_a_report(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "Found an error, or want something added?" in body
        assert "Open an issue on GitHub" in body

    @needs_italian("Found an error, or want something added?")
    @needs_italian("Open an issue on GitHub")
    def test_the_italian_note_invites_a_report(self, client: TestClient):
        body = client.get(IT_URL).text
        assert "Found an error, or want something added?" not in body
        assert "Open an issue on GitHub" not in body

    def test_the_content_correction_issue_form_matches_the_link(self):
        yaml = pytest.importorskip("yaml")
        root = Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE"
        form = yaml.safe_load((root / "correzione-contenuti.yml").read_text(encoding="utf-8"))
        assert form["title"] == "[Correzione] "
        assert form["labels"] == ["contenuti"]
        fields = {b["id"]: b for b in form["body"] if "id" in b}
        assert fields["pagina"]["validations"]["required"] is True
        assert fields["errore"]["validations"]["required"] is True
        assert fields["fonte"]["validations"]["required"] is False
        (consent,) = fields["privacy"]["attributes"]["options"]
        assert consent["required"] is True
        # The link in costs_view opens exactly this form: the file name is the template= value.
        assert self.ISSUE_FORM_URL.endswith("template=correzione-contenuti.yml")
        # Blank issues are not switched off by the form: no config.yml turns them off here.
        config = root / "config.yml"
        assert not config.exists() or "blank_issues_enabled: false" not in config.read_text()

    @needs_italian("Mortgage — loan-to-value limits")
    def test_italian_page_explains_the_limits(self, client: TestClient):
        body = client.get(f"{IT_URL}?{self.BASE}&mortgage=90&mortgage_unit=pct").text
        assert "limiti di finanziabilità" in body
        assert "perizia" in body
        assert "€ 405.000" in body  # 90% of 450,000, in the Italian number format

    def test_result_page_form_has_the_unit_select_too(self, client: TestClient, estimate_env):
        body = client.post("/en/estimate", data=_form()).text
        section = body[body.index('id="costs"') : body.index("Adjustments")]
        assert 'name="mortgage_unit"' in section
        assert _selected(section, "cost-mortgage-unit") == "eur"
        assert 'name="mortgage"' in section
        assert "<script" not in section


class TestMortgageSlider:
    """The unit conversion and the loan-to-value slider are progressive enhancement: the server
    renders the markup (hidden) and the limits, static/costs-form.js drives them."""

    BASE = "price=450000&home=prima&rendita=1100&agency=3"

    def _field(self, body: str) -> str:
        start = body.index('id="cost-mortgage-field"')
        return body[start : body.index('id="cost-tier"', start)]

    def test_percentage_over_100_is_not_reinterpreted_as_euros(self, client: TestClient):
        # An amount left in the field after switching the unit without JavaScript.
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=300000&mortgage_unit=pct").text
        assert "The mortgage percentage must be between 0% and 100%." in body
        assert 'id="costs-result"' not in body  # no scenario was computed from it
        assert "mortgage €300,000" not in body
        assert re.search(r'id="cost-mortgage" name="mortgage"[^>]*value="300000"', body, re.S)
        assert _selected(body, "cost-mortgage-unit") == "pct"
        assert re.search(r'id="cost-price" name="price"[^>]*value="450000"', body, re.S)

    def test_euros_above_the_price_are_still_rejected_server_side(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=500000&mortgage_unit=eur").text
        assert "The mortgage cannot exceed the purchase price." in body
        assert 'id="costs-result"' not in body
        assert _selected(body, "cost-mortgage-unit") == "eur"

    def test_slider_markup_and_both_labels_render_in_percentage_mode(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=85&mortgage_unit=pct").text
        field = self._field(body)
        slider = re.search(r'<input[^>]*type="range"[^>]*>', field, re.S)
        assert slider
        tag = slider.group(0)
        assert 'min="0"' in tag and 'max="100"' in tag and 'step="1"' in tag
        assert 'aria-label="Mortgage, as a percentage of the price"' in tag
        assert 'aria-describedby="cost-ltv-legend"' in tag
        assert "name=" not in tag  # never submitted: the number input is the one source
        assert "80%: ordinary mutuo fondiario limit" in field
        assert "80% to 100%: integrative guarantees required" in field
        assert "Above the ordinary 80% limit, the rules allow up to 100% only with integrative" in (
            field
        )
        # The wording states the rule, never that the visitor qualifies.
        assert "not an assessment of your case" in field

    def test_the_no_javascript_form_is_untouched(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.BASE}&mortgage=85&mortgage_unit=pct").text
        field = self._field(body)
        number = re.search(r'<input[^>]*id="cost-mortgage"[^>]*>', field, re.S).group(0)
        # No constraint a plain submit could trip over: the script adds max/step per unit.
        assert 'step="any"' in number and "max=" not in number
        # Everything the script reveals starts hidden, so no dead control shows without it.
        assert re.search(r'<div class="ltv"[^>]*\bhidden>', field)
        assert re.search(r'id="cost-mortgage-error"[^>]*\bhidden>', field)
        assert re.search(r'id="cost-ltv-caution"[^>]*\bhidden>', field)
        assert "onchange" not in field and "oninput" not in field and "<script" not in field

    def test_markup_is_also_there_in_euro_mode_so_a_switch_can_reveal_it(self, client: TestClient):
        body = client.get(EN_URL).text
        assert _selected(body, "cost-mortgage-unit") == "eur"
        assert 'type="range"' in self._field(body)

    def test_limits_come_from_rates_not_from_the_template_or_the_script(
        self, client: TestClient, monkeypatch
    ):
        body = client.get(EN_URL).text
        field = self._field(body)
        assert 'data-ltv-limit="80"' in field and 'data-ltv-max="100"' in field
        assert f'data-ltv-limit="{rates.MUTUO_FONDIARIO_MAX_LTV_PCT:g}"' in field
        assert "--ltv-frac: 0.8" in field
        monkeypatch.setattr(rates, "MUTUO_FONDIARIO_MAX_LTV_PCT", 75.0)
        monkeypatch.setattr(rates, "MUTUO_FONDIARIO_MAX_LTV_WITH_GUARANTEES_PCT", 95.0)
        field = self._field(client.get(EN_URL).text)
        assert 'data-ltv-limit="75"' in field and 'data-ltv-max="95"' in field
        assert 'max="95"' in re.search(r'<input[^>]*type="range"[^>]*>', field, re.S).group(0)
        assert "75%: ordinary mutuo fondiario limit" in field
        assert "75% to 95%: integrative guarantees required" in field

    def test_client_side_messages_are_the_server_messages(self, client: TestClient):
        field = self._field(client.get(EN_URL).text)
        assert 'data-msg-over-price="The mortgage cannot exceed the purchase price."' in field
        assert 'data-msg-pct-range="The mortgage percentage must be between 0% and 100%."' in field
        assert 'data-of-price="of the price"' in field

    def test_italian_page_reuses_the_translated_validation_messages(self, client: TestClient):
        field = self._field(client.get(IT_URL).text)
        assert 'data-ltv-limit="80"' in field
        assert "Il mutuo non può superare il prezzo di acquisto." in field
        assert "La percentuale del mutuo deve essere compresa tra 0% e 100%." in field
        assert "80%: limite ordinario del mutuo fondiario" in field
        assert "servono garanzie integrative" in field

    @pytest.mark.parametrize("path", [IT_URL, EN_URL])
    def test_calculator_page_loads_the_script_and_the_file_is_served(
        self, client: TestClient, path: str
    ):
        body = client.get(path).text
        src = re.search(r'<script src="(/static/costs-form\.js\?v=[0-9a-f]+)"></script>', body)
        assert src
        js = client.get(src.group(1))
        assert js.status_code == 200
        assert "cost-mortgage-field" in js.text
        assert "immutable" in js.headers["cache-control"]

    def test_result_page_loads_the_script_outside_the_form(self, client: TestClient, estimate_env):
        body = client.post("/en/estimate", data=_form()).text
        tag = '<script src="/static/costs-form.js?v='
        assert body.count(tag) == 1
        section = body[body.index('id="costs"') : body.index("Adjustments")]
        assert tag not in section  # the section itself stays script-free
        assert body.index(tag) > body.index("cost-mortgage-field")
        assert 'type="range"' in section


class TestResultPageSection:
    def test_section_renders_below_the_verdict_and_links_to_the_calculator(
        self, client: TestClient, estimate_env
    ):
        body = client.post("/en/estimate", data=_form()).text
        assert body.index('id="costs"') > body.index("verdict-hero")
        assert body.index('id="costs"') < body.index("Adjustments")
        assert "What it really costs" in body
        assert f'href="{EN_URL}#method"' in body

    def test_form_works_without_javascript(self, client: TestClient, estimate_env):
        body = client.post("/en/estimate", data=_form()).text
        section = body[body.index('id="costs"') : body.index("Adjustments")]
        form = re.search(r'<form method="get" action="([^"]+)" class="costs-form">', section)
        assert form and form.group(1) == f"{EN_URL}#costs-result"
        assert "<script" not in section
        assert "onclick" not in section
        assert "onchange" not in section
        assert 'type="submit"' in section

    def test_prefilled_from_the_property(self, client: TestClient, estimate_env):
        body = client.post(
            "/en/estimate",
            data=_form(fine_condition="da ristrutturare", omi_condition_override="NORMALE"),
        ).text
        assert re.search(r'id="cost-price" name="price"[^>]*value="450000"', body, re.S)
        assert re.search(r'id="cost-surface" name="surface"[^>]*value="70"', body, re.S)
        assert _selected(body, "cost-tier") == "full"  # da ristrutturare -> full
        assert _selected(body, "cost-home") == "prima"
        assert _selected(body, "cost-seller") == "private"

    @pytest.mark.parametrize("condition", ["abitabile", "ristrutturato", "nuovo"])
    def test_move_in_conditions_default_to_no_renovation(
        self, client: TestClient, estimate_env, condition: str
    ):
        body = client.post("/en/estimate", data=_form(fine_condition=condition)).text
        assert _selected(body, "cost-tier") == "none"

    def test_result_page_and_calculator_agree_for_the_same_inputs(
        self, client: TestClient, estimate_env
    ):
        body = client.post(
            "/en/estimate",
            data=_form(fine_condition="da ristrutturare", omi_condition_override="NORMALE"),
        ).text
        calc = client.get(
            f"{EN_URL}?price=450000&surface=70&home=prima&seller=private&category=A/2"
            "&rendita=&agency=3&mortgage=&tier=full"
        ).text
        assert _total(calc) == _total(body)

    def test_italian_result_page_links_to_the_italian_calculator(
        self, client: TestClient, estimate_env
    ):
        body = client.post("/it/estimate", data=_form()).text
        assert f'action="{IT_URL}#costs-result"' in body

    def test_share_view_also_renders_the_section(self, client: TestClient, estimate_env):
        body = client.post("/en/estimate", data=_form()).text
        share_path = re.search(r'data-url="https?://[^/]+(/s/[A-Za-z0-9]+)"', body)
        assert share_path
        shared = client.get(share_path.group(1), headers={"Accept-Language": "en"})
        assert shared.status_code == 200
        assert 'id="costs"' in shared.text

    def test_verdict_is_untouched_by_the_cost_inputs(self, client: TestClient, estimate_env):
        a = client.post("/en/estimate", data=_form(fine_condition="abitabile")).text
        verdict = re.search(r"Verdict · (Under-priced|Fair|Over-priced)", a)
        assert verdict


# ---------------------------------------------------------------------------
# Interest button
# ---------------------------------------------------------------------------


class TestInterestButton:
    def test_hidden_and_inert_when_the_env_var_is_unset(
        self, client: TestClient, estimate_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("STIMMO_DOSSIER_INTEREST", raising=False)
        view, click = _counter("view", "en"), _counter("click", "en")
        body = client.post("/en/estimate", data=_form()).text
        assert "dossier-interest" not in body
        assert "interest-form" not in body
        assert "Negotiation dossier" not in body
        assert _counter("view", "en") == view
        r = client.post("/en/dossier-interest")
        assert r.status_code == 404
        assert _counter("click", "en") == click

    @pytest.mark.parametrize("value", ["", "0", "false", "off", "no"])
    def test_falsy_values_keep_it_off(
        self, client: TestClient, estimate_env, monkeypatch: pytest.MonkeyPatch, value: str
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", value)
        assert "interest-form" not in client.post("/en/estimate", data=_form()).text
        assert client.post("/en/dossier-interest").status_code == 404

    def test_renders_clearly_marked_as_not_available_and_counts_a_view(
        self, client: TestClient, estimate_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        before = _counter("view", "en")
        body = client.post("/en/estimate", data=_form()).text
        assert 'id="interest-form"' in body
        assert "Negotiation dossier · €29 · coming soon" in body
        assert "not available yet" in body
        assert "sensitivity table" in body
        assert "walk-away" in body
        assert "due-diligence checklist" in body
        assert _counter("view", "en") == before + 1
        assert 'method="post" action="/en/dossier-interest"' in body

    @needs_italian("Negotiation dossier · %(price)s · coming soon")
    def test_italian_copy_is_translated(
        self, client: TestClient, estimate_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        body = client.post("/it/estimate", data=_form()).text
        # Wording-agnostic: the Italian product name is owner-reviewed copy.
        assert "Negotiation dossier" not in body
        assert "in arrivo" in body

    def test_italian_view_is_counted_under_the_it_label(
        self, client: TestClient, estimate_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        before_it, before_en = _counter("view", "it"), _counter("view", "en")
        body = client.post("/it/estimate", data=_form()).text
        assert 'action="/it/dossier-interest"' in body
        assert "Negotiation dossier" not in body or _italian_missing(
            "Negotiation dossier · %(price)s · coming soon"
        )
        assert _counter("view", "it") == before_it + 1
        assert _counter("view", "en") == before_en

    def test_not_rendered_on_share_views_and_not_counted(
        self, client: TestClient, estimate_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        body = client.post("/en/estimate", data=_form()).text
        share_path = re.search(r'data-url="https?://[^/]+(/s/[A-Za-z0-9]+)"', body)
        assert share_path
        before = _counter("view", "en")
        shared = client.get(share_path.group(1), headers={"Accept-Language": "en"})
        assert shared.status_code == 200
        assert "interest-form" not in shared.text
        assert "Negotiation dossier" not in shared.text
        assert _counter("view", "en") == before

    def test_click_returns_204_counts_and_sets_nothing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        before = _counter("click", "en")
        r = client.post("/en/dossier-interest")
        assert r.status_code == 204
        assert r.content == b""
        assert "set-cookie" not in r.headers
        assert r.headers["cache-control"] == "no-store"
        assert _counter("click", "en") == before + 1

    def test_click_is_labelled_by_language(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        before_it, before_en = _counter("click", "it"), _counter("click", "en")
        assert client.post("/it/dossier-interest").status_code == 204
        assert _counter("click", "it") == before_it + 1
        assert _counter("click", "en") == before_en

    def test_cross_site_post_is_refused_and_not_counted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        before = _counter("click", "en")
        r = client.post("/en/dossier-interest", headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
        assert _counter("click", "en") == before
        ok = client.post("/en/dossier-interest", headers={"Sec-Fetch-Site": "same-origin"})
        assert ok.status_code == 204

    def test_unsupported_language_is_rejected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        assert client.post("/fr/dossier-interest").status_code in (404, 405, 422)

    def test_label_set_is_event_and_lang_only(self):
        assert DOSSIER_INTEREST._labelnames == ("event", "lang")
        assert DOSSIER_INTEREST._name == "stimmo_dossier_interest"

    def test_click_is_labelled_by_endpoint_name_at_the_dispatcher(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("STIMMO_DOSSIER_INTEREST", "1")
        before = REQUESTS.labels(method="POST", route="dossier_interest", status="204")._value.get()
        r = TestClient(application).post("/en/dossier-interest")
        assert r.status_code == 204
        after = REQUESTS.labels(method="POST", route="dossier_interest", status="204")._value.get()
        assert after == before + 1

    def test_is_not_a_html_response_route(self):
        route = next(
            r for r in app.routes if getattr(r, "path", None) == "/{lang}/dossier-interest"
        )
        assert route.status_code == 204
        assert "POST" in route.methods


# ---------------------------------------------------------------------------
# About / privacy copy
# ---------------------------------------------------------------------------


class TestCopy:
    def test_about_has_a_costs_section_and_keeps_its_claims(self, client: TestClient):
        body = client.get("/en/about").text
        assert "What buying it costs" in body
        assert f'href="{EN_URL}"' in body
        assert "costs/rates.py" in body
        # The existing deterministic / no-ML claims are untouched.
        assert "No machine learning." in body
        # Two tuning surfaces, stated as such: no claim that one file holds everything.
        assert "entire tuning surface" not in body
        assert "valuation/adjustments.py</span> — and transaction-cost" in " ".join(body.split())
        assert "costs/rates.py" in body
        assert "same inputs, same output" in body.lower() or "Same inputs, same output" in body
        assert "valuation/adjustments.py" in body

    def test_about_and_privacy_lastmod_is_bumped_and_hubs_are_untouched(self, client: TestClient):
        from stimmo.data import omi, zones
        from stimmo.web.app import _STATIC_PAGE_COPY_DATE, _semester_start_date

        _sitemap_xml.cache_clear()
        body = client.get("/sitemap.xml").text

        def lastmod(loc: str) -> str | None:
            marker = f"<loc>https://stimmo.it{loc}</loc>"
            i = body.index(marker) + len(marker)
            m = re.match(r"\s*<lastmod>([^<]+)</lastmod>", body[i:])
            return m.group(1) if m else None

        own = app_module._ABOUT_PRIVACY_COPY_DATE.isoformat()
        assert own == "2026-10-07"
        hub = app_module._zones_index_lastmod({}).isoformat()
        for lang in ("it", "en"):
            assert lastmod(f"/{lang}/about") == own
            assert lastmod(f"/{lang}/privacy") == own
            # form and bookmarklet did not change: they keep the shared date.
            assert lastmod(f"/{lang}/") == _STATIC_PAGE_COPY_DATE.isoformat()
            assert lastmod(f"/{lang}/bookmarklet") == _STATIC_PAGE_COPY_DATE.isoformat()
            # The zones hub keeps its own date, and a zone page the OMI semester start.
            assert lastmod(f"/{lang}/zones") == hub
            code, _descr = zones.list_zones()[0]
            assert (
                lastmod(f"/{lang}/zones/{code}") == _semester_start_date(omi.semester()).isoformat()
            )

    def test_about_italian_links_to_the_italian_calculator(self, client: TestClient):
        assert f'href="{IT_URL}"' in client.get("/it/about").text

    def test_privacy_mentions_the_anonymous_click_counter(self, client: TestClient):
        body = client.get("/en/privacy").text
        assert "anonymous counter" in body
        assert "no listing data, cookie or identifier" in body

    @needs_italian(
        "If the result page offers a feature that is not available yet, clicking its interest "
        "button adds one to an anonymous counter kept by interface language only: the request "
        "carries no listing data, cookie or identifier, and nothing else about it is stored."
    )
    def test_privacy_sentence_is_translated(self, client: TestClient):
        assert "anonymous counter" not in client.get("/it/privacy").text

    def test_no_new_link_on_hub_pages(self, client: TestClient):
        """The neighbourhood hubs and the zones pages do not link the calculator from
        their own content. The site-wide header nav is the one link that may (it is a separate,
        holdable change: see tests/test_costs_nav.py), so it is left out of this check."""
        for path in ("/it/zones", "/en/zones", "/it/zones/B12", "/en/zones/B12"):
            body = client.get(path).text
            body = re.sub(r'<nav class="topbar">.*?</nav>', "", body, flags=re.S)
            assert "costi-acquisto-casa-milano" not in body
            assert "home-buying-costs" not in body


# ---------------------------------------------------------------------------
# Label rendering and form parsing
# ---------------------------------------------------------------------------


def _rich_lines():
    """Lines covering every code the calculator can emit, in several shapes."""
    from stimmo.costs.calculator import compute

    scenarios = [
        PurchaseScenario(
            price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_eur=300_000,
            surface_m2=70, renovation_tier=RenovationTier.MEDIUM,
        ),
        PurchaseScenario(price_eur=450_000, prima_casa=True, rendita_eur=200),
        PurchaseScenario(price_eur=450_000, prima_casa=True),
        PurchaseScenario(price_eur=900_000, prima_casa=False, mortgage_eur=500_000),
        PurchaseScenario(
            price_eur=450_000, prima_casa=True, seller=SellerType.BUILDER_VAT, agency_pct=2.5,
            category=CadastralCategory.A1, surface_m2=40, renovation_tier=RenovationTier.PREMIUM,
        ),
        PurchaseScenario(price_eur=80_000, prima_casa=True),
        # Ranges past their review date: the overdue note.
        PurchaseScenario(
            price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_eur=300_000,
            surface_m2=70, renovation_tier=RenovationTier.MEDIUM, as_of=date(2027, 10, 7),
        ),
        # Every LTV note at once: above 80 %, Consap, 100 %, computed on the price.
        PurchaseScenario(price_eur=200_000, prima_casa=True, rendita_eur=900, mortgage_ltv_pct=100),
        # A mortgage with a rate: the payment's variable-rate note.
        PurchaseScenario(
            price_eur=450_000, prima_casa=True, rendita_eur=1_100, mortgage_eur=300_000,
            mortgage_years=20, mortgage_rate_pct=3.4,
        ),
    ]  # fmt: skip
    breakdowns = [compute(s) for s in scenarios]
    return [ln for b in breakdowns for ln in b.lines], [n for b in breakdowns for n in b.notes]


class TestLabels:
    def test_every_emitted_line_and_note_has_a_real_label(self):
        lines, notes = _rich_lines()
        assert {ln.code for ln in lines} >= {
            "registro_tax", "ipotecaria_catastale", "vat_builder", "preliminary_contract",
            "notary_purchase", "agency_fee", "substitute_tax_mortgage", "notary_mortgage",
            "mortgage_instruction_fee", "mortgage_appraisal", "fire_insurance",
            "conformity_check", "moving_utilities", "renovation_works",
            "renovation_contingency", "renovation_vat", "renovation_fees", "renovation_bonus",
        }  # fmt: skip
        assert {"ranges_overdue", "mortgage_rate_missing", "mortgage_variable_rate"} <= {
            n.code for n in notes
        }
        for locale in ("en_US", "it_IT"):
            for ln in lines:
                text = labels.render_cost_line(ln, locale)
                assert text and text != ln.code, (locale, ln.code)
                assert "%(" not in text and "?" not in text.replace("? ", ""), (locale, text)
            for n in notes:
                text = labels.render_cost_note(n, locale)
                assert text and text != n.code, (locale, n.code)
                assert "%(" not in text

    @needs_italian("Notary fee — purchase deed, incl. IVA")
    @needs_italian("Agency fee (%(pct)s + IVA %(vat)s)")
    @needs_italian("Cadastral and planning conformity check")
    @needs_italian("Technical fees and filings (incl. CILA)")
    def test_italian_labels_are_translated(self):
        lines, _ = _rich_lines()
        by_code = {ln.code: ln for ln in lines}
        for code in ("agency_fee", "notary_purchase", "conformity_check", "renovation_fees"):
            en = labels.render_cost_line(by_code[code], "en_US")
            it = labels.render_cost_line(by_code[code], "it_IT")
            assert en != it, code

    def test_every_ltv_note_code_has_a_real_label(self):
        _, notes = _rich_lines()
        ltv = {n.code: n for n in notes if n.code.startswith(("ltv_", "consap"))}
        assert set(ltv) == {
            "ltv_above_limit", "consap_fund", "ltv_full_financing", "ltv_on_price_not_appraisal"
        }  # fmt: skip
        for locale in ("en_US", "it_IT"):
            for n in ltv.values():
                text = labels.render_cost_note(n, locale)
                assert text != n.code and "%(" not in text and "?" not in text, (locale, n.code)
        en = labels.render_cost_note(ltv["ltv_above_limit"], "en_US")
        assert "limit is 80%" in en and "up to 100%" in en and "ask it" in en
        consap = labels.render_cost_note(ltv["consap_fund"], "en_US")
        for fact in ("€250,000", "50%", "80%", "€40,000", "A/1, A/8 or A/9"):
            assert fact in consap, fact
        assert "December 31, 2027" in consap
        assert "31 dicembre 2027" in labels.render_cost_note(ltv["consap_fund"], "it_IT")

    def test_the_consap_note_states_what_the_official_page_states(self):
        """https://www.consap.it/fondo-prima-casa/ (updated 30 September 2026): access is reserved
        to priority categories; two separate higher-guarantee rules; applicant and property
        conditions. The ISEE figures of the large families are access limits, not guarantee ones."""
        _, notes = _rich_lines()
        note = next(n for n in notes if n.code == "consap_fund")
        text = labels.render_cost_note(note, "en_US")
        # Reserved to priority categories, the new ones included and dated.
        assert "Access is reserved to priority categories" in text
        for category in (
            "young couples", "single-parent households with minor children", "IACP",
            "people under 36", "large families", "permanent disability",
        ):  # fmt: skip
            assert category in text, category
        assert "from August 3, 2026" in text
        # Applicant and property conditions, including the one that cannot be computed.
        assert "must not own another home, including abroad" in text
        assert "main residence" in text
        assert "luxury features" in text and "1072 of 2 August 1969" in text
        # Two separate rules for the higher guarantee.
        assert "can be raised to 80%" in text
        assert "for the disability categories with an ISEE of at most €40,000" in text
        assert "above 80% of the purchase price including accessory costs" in text
        assert "with an ISEE of at most €40,000 (applications until December 31, 2027)" in text
        # The access limits of the large families are not presented as guarantee thresholds.
        assert "€45,000" not in text and "€50,000" not in text
        assert "for large families)" not in text
        # And the note still ends the way every LTV note does.
        assert "Check Consap's conditions and ask the bank." in text
        assert text.endswith("This is general information, not an assessment of any buyer.")

    def test_the_consap_note_is_a_readable_length(self):
        _, notes = _rich_lines()
        note = next(n for n in notes if n.code == "consap_fund")
        text = labels.render_cost_note(note, "en_US")
        assert text.count(". ") + 1 <= 6, text  # three body sentences + the two closing ones
        assert len(text) < 1_200

    def test_ltv_notes_state_rules_and_never_assess_the_user(self):
        """The legal boundary: general information only. No note says or implies that the
        buyer qualifies for, can obtain or can afford a loan, and none recommends a lender."""
        _, notes = _rich_lines()
        forbidden = {
            "en_US": (
                "you qualify", "you are eligible", "you can get", "you can obtain", "you will get",
                "you can afford", "you should", "we recommend", "best lender", "best mortgage",
            ),
            "it_IT": (
                "hai diritto", "sei idoneo", "sei idonea", "puoi ottenere", "ottieni",
                "puoi permetterti", "ti consigliamo", "consigliamo", "migliore banca",
                "miglior mutuo",
                # Second person only: "purché [l'immobile] non rientri nelle categorie
                # catastali" is a rule about the property, not an assessment of the buyer.
                "se rientri", "rientri tra", "rientri nei requisiti",
                "rientri nelle categorie prioritarie",
            ),
        }  # fmt: skip
        for locale, phrases in forbidden.items():
            for n in notes:
                if not n.code.startswith(("ltv_", "consap")):
                    continue
                text = labels.render_cost_note(n, locale).lower()
                assert not [p for p in phrases if p in text], (locale, n.code, text)

    def test_the_approximation_caveat_says_the_ratio_is_on_the_price(self):
        _, notes = _rich_lines()
        note = next(n for n in notes if n.code == "ltv_on_price_not_appraisal")
        text = labels.render_cost_note(note, "en_US")
        assert "computed on the price" in text and "appraisal" in text
        assert "approximation" in text

    @needs_italian("%(down)s down payment + %(costs)s costs")
    @needs_italian("Mortgage — loan-to-value limits")
    def test_italian_ltv_notes_are_translated(self):
        _, notes = _rich_lines()
        for n in notes:
            if n.code.startswith(("ltv_", "consap")):
                assert labels.render_cost_note(n, "it_IT") != labels.render_cost_note(n, "en_US"), (
                    n.code
                )

    @needs_italian("indicative")
    def test_status_tags_are_translated(self):
        from stimmo.models import CostStatus

        for status in CostStatus:
            assert labels.status_label(status, "en_US")
        assert labels.status_label(CostStatus.INDICATIVE, "it_IT") != "indicative"

    def test_formats_follow_the_locale(self):
        lines, _ = _rich_lines()
        registro = next(
            ln for ln in lines if ln.code == "registro_tax" and "cadastral_value_eur" in ln.params
        )
        assert "€127,050" in labels.render_cost_line(registro, "en_US")
        assert "€ 127.050" in labels.render_cost_line(registro, "it_IT")


class TestParse:
    def test_defaults_for_a_minimal_query(self):
        parsed = costs_view.parse({"price": "450000"}, today=date(2026, 10, 7))
        s = parsed.scenario
        assert s is not None and not parsed.errors
        assert s.prima_casa is True
        assert s.seller is SellerType.PRIVATE
        assert s.category is CadastralCategory.A2
        assert s.renovation_tier is RenovationTier.NONE
        assert s.agency_pct == rates.AGENCY_DEFAULT_PCT
        assert s.rendita_eur is None
        assert s.mortgage_eur == 0

    def test_mortgage_unit_defaults_to_euros(self):
        parsed = costs_view.parse({"price": "450000", "mortgage": "300000"})
        s = parsed.scenario
        assert parsed.values["mortgage_unit"] == "eur"
        assert s.mortgage_eur == 300_000 and s.mortgage_ltv_pct is None

    def test_percentage_unit_builds_an_ltv_scenario(self):
        parsed = costs_view.parse({"price": "450000", "mortgage": "80", "mortgage_unit": "pct"})
        s = parsed.scenario
        assert not parsed.errors
        # Exactly one of the two is set.
        assert s.mortgage_ltv_pct == 80 and s.mortgage_eur == 0
        assert s.mortgage_amount_eur == 360_000

    def test_amount_and_percentage_inputs_give_the_same_scenario_amount(self):
        by_pct = costs_view.parse({"price": "450000", "mortgage": "80", "mortgage_unit": "pct"})
        by_eur = costs_view.parse({"price": "450000", "mortgage": "360000"})
        assert by_pct.scenario.mortgage_amount_eur == by_eur.scenario.mortgage_amount_eur

    def test_percentage_accepts_a_decimal_comma_and_the_bounds(self):
        for raw, expected in (("66,5", 66.5), ("0", 0.0), ("100", 100.0)):
            parsed = costs_view.parse({"price": "450000", "mortgage": raw, "mortgage_unit": "pct"})
            assert not parsed.errors, raw
            assert parsed.scenario.mortgage_ltv_pct == expected

    @pytest.mark.parametrize("raw", ["100.01", "101", "250", "1000000"])
    def test_a_percentage_above_100_is_a_validation_error(self, raw: str):
        with use_locale("en_US"):
            parsed = costs_view.parse({"price": "450000", "mortgage": raw, "mortgage_unit": "pct"})
        assert parsed.scenario is None
        assert parsed.errors == ["The mortgage percentage must be between 0% and 100%."]
        assert parsed.values["mortgage"] == raw  # echoed back
        assert parsed.values["mortgage_unit"] == "pct"

    def test_a_negative_percentage_is_an_error(self):
        parsed = costs_view.parse({"price": "450000", "mortgage": "-5", "mortgage_unit": "pct"})
        assert parsed.scenario is None and parsed.errors

    def test_an_empty_percentage_means_no_mortgage(self):
        parsed = costs_view.parse({"price": "450000", "mortgage": "", "mortgage_unit": "pct"})
        assert parsed.scenario.mortgage_amount_eur == 0
        assert parsed.scenario.mortgage_ltv_pct is None

    def test_the_euro_limit_still_applies_to_amounts_only(self):
        # 101 is a valid amount in euros and an invalid percentage.
        assert costs_view.parse({"price": "450000", "mortgage": "101"}).scenario is not None
        assert costs_view.parse({"price": "450000", "mortgage": "450001"}).scenario is None

    def test_an_unknown_unit_is_an_error(self):
        with use_locale("en_US"):
            parsed = costs_view.parse({"price": "450000", "mortgage": "80", "mortgage_unit": "usd"})
        assert parsed.scenario is None
        assert parsed.errors == ["Unknown option “usd”."]

    def test_assumptions_show_amount_and_share_of_the_price(self):
        parsed = costs_view.parse({"price": "450000", "mortgage": "80", "mortgage_unit": "pct"})
        with use_locale("en_US"):
            text = costs_view.assumptions(parsed.scenario)
        assert "mortgage €360,000 (80% of the price)" in text

    def test_comma_decimal_is_accepted(self):
        parsed = costs_view.parse({"price": "450000", "rendita": "1100,50", "agency": "2,5"})
        assert parsed.scenario.rendita_eur == 1100.5
        assert parsed.scenario.agency_pct == 2.5

    def test_not_prima_casa(self):
        assert costs_view.parse({"price": "450000", "home": "other"}).scenario.prima_casa is False

    def test_errors_are_collected_and_values_are_echoed(self):
        parsed = costs_view.parse({"price": "450000", "mortgage": "999999", "tier": "bogus"})
        assert parsed.scenario is None
        assert parsed.errors
        assert parsed.values["mortgage"] == "999999"

    @pytest.mark.parametrize(
        ("price", "expected"),
        [(450_000, "450000"), (1_234_567, "1234567"), (2_500_000.5, "2500000.5")],
    )
    def test_large_prices_are_never_written_in_exponent_form(self, price, expected):
        from stimmo.models import (
            ConstructionEra,
            FineCondition,
            OmiCondition,
            Orientation,
            Property,
            PropertyType,
        )

        prop = Property(
            address="Via X, Milano",
            surface_m2=100,
            property_type=PropertyType.SIGNORILI,
            omi_condition=OmiCondition.OTTIMO,
            fine_condition=FineCondition.NUOVO,
            floor=3,
            total_floors=6,
            has_lift=True,
            construction_era=ConstructionEra.RECENT,
            orientation=Orientation.SOUTH,
            asking_price_eur=price,
        )
        values = costs_view.values_for_property(prop)
        assert values["price"] == expected
        assert costs_view.parse(values).scenario.price_eur == price

    def test_values_for_property_prefill(self):
        from stimmo.models import (
            ConstructionEra,
            FineCondition,
            OmiCondition,
            Orientation,
            Property,
            PropertyType,
        )

        prop = Property(
            address="Via X, Milano",
            surface_m2=62.5,
            property_type=PropertyType.CIVILI,
            omi_condition=OmiCondition.SCADENTE,
            fine_condition=FineCondition.DA_RISTRUTTURARE,
            floor=1,
            total_floors=4,
            has_lift=False,
            construction_era=ConstructionEra.PRE_WAR,
            orientation=Orientation.MIXED,
            asking_price_eur=312_500,
        )
        values = costs_view.values_for_property(prop)
        assert values["price"] == "312500"
        assert values["surface"] == "62.5"
        assert values["tier"] == "full"
        assert costs_view.parse(values).scenario is not None

    @needs_italian("Prima casa — main home, first-home tax regime")
    def test_the_label_context_is_rendered_under_the_active_locale(self):
        with use_locale("it_IT"):
            opts = costs_view.options()
        assert opts["homes"][0][1] != "Prima casa — main home, first-home tax regime"


# ---------------------------------------------------------------------------
# Freshness: ranges past their review date label themselves
# ---------------------------------------------------------------------------

BADGE = 'class="tag stale"'
EN_NOTE = "of the ranges below are past their scheduled review"
# The worked example on the standalone page (mortgage, medium renovation, default agency) has
# these benchmark-backed lines; the result page of a "da ristrutturare" listing has no mortgage.
_BEFORE_DUE = date(2027, 4, 6)
_SIX_MONTHS_ON = date(2027, 4, 7)
_TWELVE_MONTHS_ON = date(2027, 10, 7)


def _costs_section(body: str) -> str:
    return body[body.index('id="costs"') : body.index("Adjustments")]


class TestStaleBadge:
    @pytest.fixture()
    def at(self, monkeypatch: pytest.MonkeyPatch):
        def freeze(day: date) -> None:
            monkeypatch.setattr(app_module, "_today", lambda: day)

        return freeze

    @pytest.mark.parametrize("path", [IT_URL, EN_URL])
    @pytest.mark.parametrize("day", [date(2026, 10, 8), _BEFORE_DUE])
    def test_calculator_page_before_a_due_date_has_neither_badge_nor_note(
        self, client: TestClient, at, path: str, day: date
    ):
        at(day)
        body = client.get(path).text
        assert BADGE not in body
        assert "ranges_overdue" not in body
        assert EN_NOTE not in body

    def test_calculator_page_labels_the_six_month_ranges_first(self, client: TestClient, at):
        at(_SIX_MONTHS_ON)
        body = client.get(EN_URL).text
        # Bank fees (3) and renovation works, contingency and technical fees (3).
        assert body.count(BADGE) == 6
        assert "6 of the ranges below are past their scheduled review" in body
        assert "last reviewed October 6, 2026" in body
        assert "marked “to be checked”" in body

    def test_calculator_page_labels_the_twelve_month_ranges_later(self, client: TestClient, at):
        at(_TWELVE_MONTHS_ON)
        body = client.get(EN_URL).text
        # + notary (purchase, mortgage), agency, conformity check, moving and utilities.
        assert body.count(BADGE) == 11
        assert "11 of the ranges below are past their scheduled review" in body

    def test_the_badge_sits_on_the_line_it_is_about(self, client: TestClient, at):
        at(_SIX_MONTHS_ON)
        body = client.get(EN_URL).text
        row = lambda text: re.search(rf"<tr>(?:(?!</tr>).)*{text}.*?</tr>", body, re.S).group(0)  # noqa: E731
        assert BADGE in row("Bank valuation")
        assert BADGE not in row("Imposta ipotecaria")
        assert BADGE not in row("Notary fee — purchase deed")  # a 12-month range, still in date
        assert BADGE not in row("IVA on works and contingency")  # statutory rate, flagged base

    def test_the_note_is_marked_as_a_warning_like_a_missing_rendita(self, client: TestClient, at):
        at(_SIX_MONTHS_ON)
        body = client.get(EN_URL).text
        assert re.search(
            r'<div class="note"[^>]*border-left-color:var\(--warn\)[^>]*>[^<]*6 of the ranges', body
        )

    @pytest.mark.parametrize("day", [date(2026, 10, 8), _BEFORE_DUE])
    def test_result_page_before_a_due_date_has_neither(
        self, client: TestClient, estimate_env, at, day: date
    ):
        at(day)
        body = client.post(
            "/en/estimate",
            data=_form(fine_condition="da ristrutturare", omi_condition_override="NORMALE"),
        ).text
        assert BADGE not in _costs_section(body)
        assert EN_NOTE not in body

    def test_result_page_shows_badge_and_note_past_a_due_date(
        self, client: TestClient, estimate_env, at
    ):
        at(_SIX_MONTHS_ON)
        body = client.post(
            "/en/estimate",
            data=_form(fine_condition="da ristrutturare", omi_condition_override="NORMALE"),
        ).text
        section = _costs_section(body)
        # No mortgage on a result page: works, contingency and technical fees.
        assert section.count(BADGE) == 3
        assert "3 of the ranges below are past their scheduled review" in section

    def test_share_view_follows_the_same_rule(self, client: TestClient, estimate_env, at):
        at(date(2026, 10, 8))
        body = client.post(
            "/en/estimate",
            data=_form(fine_condition="da ristrutturare", omi_condition_override="NORMALE"),
        ).text
        share_path = re.search(r'data-url="https?://[^/]+(/s/[A-Za-z0-9]+)"', body).group(1)
        assert BADGE not in client.get(share_path, headers={"Accept-Language": "en"}).text
        at(_SIX_MONTHS_ON)
        shared = client.get(share_path, headers={"Accept-Language": "en"}).text
        assert shared.count(BADGE) == 3 and EN_NOTE in shared

    def test_a_submitted_scenario_is_judged_against_the_same_date(self, client: TestClient, at):
        at(_SIX_MONTHS_ON)
        body = client.get(f"{EN_URL}?price=450000&mortgage=300000&agency=2.5").text
        assert body.count(BADGE) == 3  # appraisal, istruttoria, fire insurance
        assert "3 of the ranges below are past their scheduled review" in body

    def test_italian_page_has_the_badges_and_the_note(self, client: TestClient, at):
        at(_SIX_MONTHS_ON)
        body = client.get(IT_URL).text
        assert body.count(BADGE) == 6
        assert "6 ottobre 2026" in body  # the note's review date, in Italian

    @needs_italian("to be checked")
    def test_italian_page_words_are_translated(self, client: TestClient, at):
        at(_SIX_MONTHS_ON)
        body = client.get(IT_URL).text
        assert EN_NOTE not in body
        assert "to be checked" not in body
        assert f">{labels.stale_label('it_IT')}</span>" in body

    def test_the_methodology_explains_the_badge_with_the_active_wording(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "re-checked against its source on a fixed schedule" in body
        assert "marked “to be checked”" in body


class TestStaleLabels:
    def test_badge_text(self):
        assert labels.stale_label("en_US") == "to be checked"

    @needs_italian("to be checked")
    def test_badge_text_is_translated(self):
        assert labels.stale_label("it_IT") != labels.stale_label("en_US")

    def test_note_text_in_both_languages(self):
        note = next(n for n in _rich_lines()[1] if n.code == "ranges_overdue")
        assert note.params == {"count": 11, "last_reviewed": "2026-10-06"}
        en = labels.render_cost_note(note, "en_US")
        assert "11 of the ranges below" in en and "October 6, 2026" in en
        assert "“to be checked”" in en and "%(" not in en
        it = labels.render_cost_note(note, "it_IT")
        assert "%(" not in it and it != note.code
        # The date and the badge wording are parameters, so they are Italian whatever the prose.
        assert "6 ottobre 2026" in it
        assert f"“{labels.stale_label('it_IT')}”" in it

    @needs_italian("to be checked")
    def test_note_prose_is_translated(self):
        note = next(n for n in _rich_lines()[1] if n.code == "ranges_overdue")
        assert labels.render_cost_note(note, "it_IT") != labels.render_cost_note(note, "en_US")


# ---------------------------------------------------------------------------
# Round 2: presentation (bold notary notes, collapsed methodology, Milan prices, exclusions)
# ---------------------------------------------------------------------------

_BIG = "price=610000&mortgage=400000&surface=70&tier=medium&mortgage_rate=3&agency=3"


def _notes(body: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r'<div class="note"[^>]*>(.*?)</div>', body, re.S)]


class TestNotaryNotes:
    def test_the_two_notes_differ_in_bold_at_a_glance(self, client: TestClient):
        body = client.get(f"{EN_URL}?{_BIG}").text
        notary = [n for n in _notes(body) if "notary fee we rely on" in n]
        assert len(notary) == 2
        assert any("<b>purchase deed</b>" in n for n in notary)
        assert any("<b>mortgage deed</b>" in n for n in notary)
        # Same sentence otherwise: bold is the only thing that tells them apart.
        plain = [re.sub(r"<[^>]+>", "", n).split(" is published")[0] for n in notary]
        assert sorted(plain) == [
            "The notary fee we rely on for the mortgage deed",
            "The notary fee we rely on for the purchase deed",
        ]

    @needs_italian(
        "The notary fee we rely on for the <b>purchase deed</b> is published for "
        "prices up to %(max)s. The figure shown is for that top band and probably "
        "understates the fee: ask the notary for a quote."
    )
    def test_italian_notes_bold_the_deed_through_the_catalog(self, client: TestClient):
        body = client.get(f"{IT_URL}?{_BIG}").text
        notary = [n for n in _notes(body) if "onorario del notaio" in n]
        assert len(notary) == 2, notary
        bold = {re.search(r"<b>(.*?)</b>", n).group(1).lower() for n in notary}
        # The catalog (not this test) chooses the Italian words; what matters is that the deed
        # is in bold, that the two differ, and that one of them is the mortgage deed.
        assert len(bold) == 2 and any("mutuo" in b for b in bold)
        plain = {re.sub(r"<b>.*?</b>", "", n).split(" è pubblicato")[0] for n in notary}
        assert len(plain) == 1  # the same sentence around the bold words
        assert "&lt;b&gt;" not in body

    @pytest.mark.parametrize("locale", ["en_US", "it_IT"])
    @pytest.mark.parametrize("kind", ["purchase", "mortgage"])
    def test_a_parameter_with_markup_is_escaped_only_the_catalog_string_is_trusted(
        self, locale: str, kind: str
    ):
        from markupsafe import Markup

        from stimmo.models import CostNote

        hostile = "<script>alert(1)</script><b>x</b>"
        note = CostNote(
            code="notary_above_published_band",
            params={"kind": kind, "band_max_eur": hostile},
        )
        text = labels.render_cost_note(note, locale)
        assert isinstance(text, Markup)
        assert "<script>" not in text and "<b>x</b>" not in text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;&lt;b&gt;x&lt;/b&gt;" in text
        assert "<b>" in text and "</b>" in text  # the catalog's own bold survives

    def test_other_notes_stay_plain_text_and_are_escaped_by_the_template(self, client: TestClient):
        from markupsafe import Markup

        from stimmo.models import CostNote

        note = CostNote(code="prima_casa_excluded_category", params={"category": "<i>A/1</i>"})
        text = labels.render_cost_note(note, "en_US")
        assert not isinstance(text, Markup) and "<i>" in text  # a plain str ...
        # ... which the template's autoescape turns into text.
        body = client.get(f"{EN_URL}?price=450000&category=A/1").text
        assert "&lt;i&gt;" not in body and "Category A/1 is excluded" in body


class TestMethodologyIsCollapsed:
    def _details(self, body: str) -> str:
        start = body.index('<details class="doc costs-details" id="method">')
        return body[start : body.index("</details>", start)]

    @pytest.mark.parametrize("path", [IT_URL, EN_URL])
    def test_a_native_details_closed_by_default_with_no_script(self, client: TestClient, path):
        body = client.get(path).text
        tag = re.search(r'<details[^>]*id="method"[^>]*>', body).group(0)
        assert " open" not in tag
        details = self._details(body)
        assert "<summary" in details and "<script" not in details and "onclick" not in details

    def test_the_content_is_in_the_dom_even_when_collapsed(self, client: TestClient):
        details = self._details(client.get(EN_URL).text)
        for text in (
            "Purchase taxes — statute",
            "Mortgage — loan-to-value limits",
            "Mortgage — monthly payment",
            "Bonus ristrutturazione",
            "Sources",
            "Agenzia delle Entrate",
        ):
            assert text in details, text

    def test_not_included_stays_outside_the_collapsed_block(self, client: TestClient):
        body = client.get(EN_URL).text
        assert 'id="not-included"' in body
        assert "IMU and TARI" in body
        assert 'id="not-included"' not in self._details(body)
        assert "Condominium arrears" not in self._details(body)
        assert body.index('id="not-included"') < body.index('id="method"')

    def test_the_summary_is_the_section_heading(self, client: TestClient):
        details = self._details(client.get(EN_URL).text)
        assert re.search(r"<summary[^>]*>\s*<h2[^>]*>How this is calculated</h2>", details)

    def test_the_result_page_links_to_the_methodology_anchor(self, client, estimate_env):
        body = client.post("/en/estimate", data=_form()).text
        assert f"{EN_URL}#method" in body

    def test_the_script_opens_it_for_a_fragment_that_points_inside(self, client: TestClient):
        js = client.get("/static/costs-form.js").text
        assert "getElementById('method')" in js and "method.open = true" in js
        assert "hashchange" in js


class TestRenovationIsMilan:
    def test_the_methodology_says_the_prices_are_milan_prices(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "Renovation — Milan prices, indicative" in body
        assert "The € per m² figures are Milan prices" in body
        assert "€ per m², Milan, ex IVA (assumed)" in body

    def test_the_works_line_and_the_subtotal_say_so(self, client: TestClient):
        body = client.get(f"{EN_URL}?{_BIG}").text
        assert "per m² (Milan prices, assumed to exclude IVA)" in body
        assert "Renovation subtotal — Milan prices" in body

    def test_the_tier_help_text_says_so(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "Works are priced per m² at Milan prices, assumed to exclude IVA" in body

    @needs_italian(
        "Works — %(tier)s renovation, %(surface)s m² × %(low)s–%(high)s per m² "
        "(Milan prices, assumed to exclude IVA)"
    )
    def test_the_line_label_carries_it_in_both_languages(self):
        from stimmo.costs.calculator import compute

        b = compute(
            PurchaseScenario(
                price_eur=450_000,
                prima_casa=True,
                surface_m2=70,
                renovation_tier=RenovationTier.FULL,
            )
        )
        works = next(ln for ln in b.lines if ln.code == "renovation_works")
        assert labels.render_cost_line(works, "en_US").endswith(
            "(Milan prices, assumed to exclude IVA)"
        )
        italian = labels.render_cost_line(works, "it_IT")
        assert italian != labels.render_cost_line(works, "en_US") and "Milano" in italian


class TestRenovationIvaBasis:
    """The cited source never says whether its €/m² include IVA, and the calculator adds IVA on
    top: so the page says "assumed to exclude IVA" wherever the figures appear and does not
    claim more than that (open question, recorded in costs/rates.py)."""

    def test_the_methodology_states_the_assumption_and_that_it_is_unconfirmed(self, client):
        body = client.get(EN_URL).text
        assert "Works are priced per m² by tier, assumed to exclude IVA" in body
        assert "does not say whether they include IVA" in body
        assert "unconfirmed assumption" in body
        assert "overstated by about that IVA" in body

    def test_it_does_not_claim_the_regional_list_proves_the_basis(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "always net of IVA" in body
        assert "not the basis of these figures" in body

    def test_the_regional_list_is_cited_for_the_convention_only(self):
        urls = {url for _title, url in costs_view.SOURCES}
        assert rates.RENOVATION_IVA_CROSSCHECK_URL in urls
        assert "prezzario" in rates.RENOVATION_IVA_CROSSCHECK_URL.lower()

    def test_the_renovation_table_stays_indicative(self, client: TestClient):
        from stimmo.costs.calculator import compute

        b = compute(
            PurchaseScenario(
                price_eur=450_000,
                prima_casa=True,
                surface_m2=70,
                renovation_tier=RenovationTier.MEDIUM,
            )
        )
        works = next(ln for ln in b.lines if ln.code == "renovation_works")
        assert works.status.value == "indicative"
        assert rates.RENOVATION_TIERS_REVIEW.indicative

    @needs_italian(
        "Works are priced per m² at Milan prices, assumed to exclude IVA. Pick the tier "
        "closest to what you expect to do."
    )
    def test_the_italian_page_carries_the_assumption_too(self, client: TestClient):
        # The catalog (not this test) words it; it must say IVA esclusa, as an assumption, on the
        # works line, in the form's help text and in the methodology.
        works = client.get(f"{IT_URL}?{_BIG}").text
        assert re.search(r"per m². \(prezzi di Milano, [^)]*IVA esclusa\)", works) or re.search(
            r"al m² \(prezzi di Milano, [^)]*IVA esclusa\)", works
        )
        assert "assumed to exclude IVA" not in works
        assert "does not say whether" not in works


class TestBonusCitations:
    """The bonus cites the statute in force and its 2027 testo-unico successor, and no source of
    it is a property portal."""

    def test_the_cap_on_total_deductions_cites_both_articles(self, client: TestClient):
        body = client.get(f"{EN_URL}?{_BIG}").text
        assert "art. 16-ter TUIR; from 2027, art. 18 of D.Lgs. 117/2026" in body
        assert "16-ter TUIR)" not in body  # never the repealed article alone

    @needs_italian(
        "A deduction from your income tax, spread over ten years. The cap on total deductions "
        "for higher incomes (art. 16-ter TUIR; from 2027, art. 18 of D.Lgs. 117/2026) is not "
        "modelled."
    )
    def test_the_italian_string_cites_both_too(self, client: TestClient):
        body = client.get(f"{IT_URL}?{_BIG}").text
        assert "16-ter" in body and "117/2026" in body
        assert "from 2027" not in body

    def test_the_bonus_sources_are_the_statute_and_the_ade_guide(self):
        titles = dict((url, title) for title, url in costs_view.SOURCES)
        assert rates.BONUS_SOURCE_URL in titles
        assert rates.AGENZIA_ENTRATE_RENOVATION_GUIDE_URL in titles
        assert titles[rates.BONUS_SOURCE_URL].startswith("Normattiva")
        assert titles[rates.AGENZIA_ENTRATE_RENOVATION_GUIDE_URL].startswith(
            "Agenzia delle Entrate"
        )

    def test_no_listed_source_is_a_property_portal_or_a_blog(self):
        for title, url in costs_view.SOURCES:
            host = url.split("/")[2]
            for banned in (
                "idealista",
                "immobiliare.it",
                "casa.it",
                "borsinoimmobiliare",
                "wikicasa",
            ):
                assert banned not in host, (title, url)
            assert "edotto" not in host, (title, url)
        body_urls = {url for _title, url in costs_view.SOURCES}
        for rule in rates.BONUS_RISTRUTTURAZIONE.values():
            assert rule.source_url in body_urls

    def test_the_bonus_row_links_to_the_statute(self, client: TestClient):
        body = client.get(f"{EN_URL}?{_BIG}").text
        rows = [r for r in re.findall(r"<tr>.*?</tr>", body, re.S) if "Bonus ristrutturazione" in r]
        assert len(rows) == 2  # works paid in 2026 and in 2027
        for row in rows:
            assert rates.BONUS_SOURCE_URL in row


class TestNotIncludedLine:
    def test_the_estimate_names_what_is_excluded_and_links_to_the_full_section(self, client):
        body = client.get(EN_URL).text
        line = re.search(r'<p class="costs-assume costs-excluded">(.*?)</p>', body, re.S).group(1)
        for term in (
            "condominium arrears",
            "works already approved",
            "IMU",
            "TARI",
            "CILA",
            "sanatoria",
        ):
            assert term in line, term
        # On the calculator page the link is a bare fragment, so the scenario in the URL stays.
        assert 'href="#not-included"' in line

    def test_the_result_page_section_has_it_too_linking_to_the_calculator(
        self, client: TestClient, estimate_env
    ):
        body = client.post("/en/estimate", data=_form()).text
        section = _costs_section(body)
        line = re.search(r'<p class="costs-assume costs-excluded">(.*?)</p>', section, re.S)
        assert line, "no 'not included' line in the result-page costs section"
        assert f'href="{EN_URL}#not-included"' in line.group(1)

    def test_italian_result_page_links_to_the_italian_section(self, client, estimate_env):
        body = client.post("/it/estimate", data=_form()).text
        start = body.index('id="costs"')
        assert f"{IT_URL}#not-included" in body[start : start + 40_000]

    def test_the_target_exists_on_the_calculator_page(self, client: TestClient):
        for path in (IT_URL, EN_URL, f"{EN_URL}?price=450000"):
            assert 'id="not-included"' in client.get(path).text


# ---------------------------------------------------------------------------
# Round 2: mortgage payment ("Rata e costo del mutuo")
# ---------------------------------------------------------------------------


def _payment_block(body: str) -> str:
    start = body.index('id="mortgage-payment"')
    return body[start : body.index('<details class="costs-lines"', start)]


class TestMortgagePaymentBlock:
    QUERY = (
        "price=450000&rendita=1100&mortgage=360000&mortgage_unit=eur"
        "&mortgage_years=25&mortgage_rate=3&agency=3"
    )

    def test_the_no_javascript_round_trip(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.QUERY}").text
        block = _payment_block(body)
        # Monthly payment (French amortisation), interest, repaid, total cost.
        assert "€1,707.16" in block
        for label in (
            "Monthly payment",
            "Total interest",
            "Total repaid",
            "Total cost of the mortgage",
        ):
            assert label in block, label
        assert "25 years at 3% TAN" in block
        assert "€152,148" in block  # total interest, whole euros
        assert "€512,148" in block  # total repaid
        # The form echoes the inputs so the same page can be re-submitted.
        assert _selected(body, "cost-mortgage-years") == "25"
        assert re.search(r'name="mortgage_rate"[^>]*value="3"', body)
        # It is a plain GET form: the new fields are inside it, with their names.
        form = re.search(r'<form method="get".*?</form>', body, re.S).group(0)
        assert 'name="mortgage_years"' in form and 'name="mortgage_rate"' in form

    def test_changing_the_term_changes_the_payment_through_the_url(self, client: TestClient):
        thirty = client.get(f"{EN_URL}?{self.QUERY.replace('years=25', 'years=30')}").text
        assert "€1,517.77" in _payment_block(thirty)  # 360,000 over 30 years at 3 %
        assert _selected(thirty, "cost-mortgage-years") == "30"

    def test_the_variable_rate_note_sits_with_the_payment(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.QUERY}").text
        block = _payment_block(body)
        assert "If your rate is variable" in block
        assert "will change as the rate moves" in block
        # ... and is not repeated in the general list of notes.
        assert body.count("will change as the rate moves") == 1

    def test_the_cost_of_the_mortgage_adds_the_mortgage_costs_to_the_interest(self, client):
        body = client.get(f"{EN_URL}?{self.QUERY}").text
        block = _payment_block(body)
        assert "interest + " in block and "mortgage costs" in block
        assert "imposta sostitutiva" in block and "insurance" in block

    def test_the_worked_example_shows_it_with_the_reference_rate_and_25_years(self, client):
        body = client.get(EN_URL).text  # 300,000 mortgage, defaults
        assert "25 years at 3.4% TAN" in _payment_block(body)
        assert _selected(body, "cost-mortgage-years") == "25"
        assert re.search(r'name="mortgage_rate"[^>]*value="3.4"', body)

    def test_the_term_options_are_the_five_documented_ones(self, client: TestClient):
        body = client.get(EN_URL).text
        select = re.search(r'<select[^>]*id="cost-mortgage-years".*?</select>', body, re.S).group(0)
        assert re.findall(r'<option value="(\d+)"', select) == ["10", "15", "20", "25", "30"]

    def test_the_assumptions_line_states_the_term_and_rate(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.QUERY}").text
        assert "25-year term at 3% TAN" in body

    def test_no_mortgage_no_block(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=450000&mortgage_rate=3.4&mortgage_years=25").text
        assert 'id="mortgage-payment"' not in body
        assert "Monthly payment" not in body

    def test_an_empty_rate_means_no_payment_and_a_prompt_with_the_reference(self, client):
        body = client.get(f"{EN_URL}?price=450000&mortgage=300000&mortgage_rate=").text
        assert 'id="mortgage-payment"' not in body
        note = next(n for n in _notes(body) if "Enter an interest rate (TAN)" in n)
        assert "3.40%" in note and "July 2026" in note and "not an offer" in note

    def test_a_link_without_the_new_fields_still_works(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=450000&mortgage=300000").text
        assert _selected(body, "cost-mortgage-years") == "25"
        assert 'id="mortgage-payment"' not in body  # no rate given, none guessed

    @pytest.mark.parametrize("query", ["mortgage_rate=21", "mortgage_rate=-1", "mortgage_rate=abc"])
    def test_a_bad_rate_shows_an_error_and_no_result(self, client: TestClient, query):
        body = client.get(f"{EN_URL}?price=450000&mortgage=300000&{query}").text
        assert 'class="note error"' in body
        assert 'id="costs-result"' not in body

    def test_an_unknown_term_is_an_error(self, client: TestClient):
        body = client.get(f"{EN_URL}?price=450000&mortgage=300000&mortgage_years=12").text
        assert "Unknown option “12”" in body and 'id="costs-result"' not in body

    def test_a_decimal_comma_rate_is_read(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.QUERY.replace('rate=3', 'rate=3,5')}").text
        assert "25 years at 3.5% TAN" in _payment_block(body)

    def test_the_payment_block_makes_no_statement_about_the_buyer(self, client: TestClient):
        """The legal boundary: a generic calculation on the amount, term and rate chosen."""
        for path in (IT_URL, EN_URL):
            block = _payment_block(client.get(f"{path}?{self.QUERY}").text).lower()
            for word in ("afford", "income", "reddito", "eligib", "idone", "qualify", "you can"):
                assert word not in block, (path, word)

    def test_no_lender_or_product_is_named_in_the_payment_surfaces(self, client: TestClient):
        body = client.get(f"{EN_URL}?{self.QUERY}").text
        section = _payment_block(body) + body[body.index('id="cost-rate-ref"') :][:2500]
        for name in (
            "Intesa",
            "UniCredit",
            "BNL",
            "Mediolanum",
            "Fineco",
            "Findomestic",
            "MutuiOnline",
            "Facile.it",
            "Mutuionline",
        ):
            assert name not in section, name

    def test_the_result_page_form_carries_the_fields_prefilled(self, client, estimate_env):
        body = client.post("/en/estimate", data=_form()).text
        section = _costs_section(body)
        assert 'name="mortgage_years"' in section and 'name="mortgage_rate"' in section
        assert _selected(section, "cost-mortgage-years") == "25"
        assert re.search(r'name="mortgage_rate"[^>]*value="3.4"', section)
        assert 'id="mortgage-payment"' not in section  # no mortgage until the visitor sets one

    def test_the_payment_does_not_change_the_verdict(self, client, estimate_env):
        plain = client.post("/en/estimate", data=_form()).text
        assert "Monthly payment" not in plain


class TestRateReference:
    def test_it_is_shown_beside_the_field_with_its_month_and_source(self, client: TestClient):
        body = client.get(EN_URL).text
        ref = body[body.index('id="cost-rate-ref"') :]
        ref = ref[: ref.index("</div>")]
        assert "Market averages" in ref
        assert "Banca d'Italia, July 2026" in ref
        assert "provisional" in ref
        assert rates.MORTGAGE_RATE_SOURCE_URL in ref
        for value in ("3.40%", "2.99%", "3.56%"):
            assert value in ref, value
        assert "up to 1 year" in ref and "over 1 year" in ref

    def test_the_taeg_is_called_a_taeg_and_explained_in_a_line(self, client: TestClient):
        body = client.get(EN_URL).text
        ref = body[body.index('id="cost-rate-ref"') :]
        ref = ref[: ref.index("</div>")]
        assert "TAEG (3.81% on average)" in ref
        assert "also counts fees and mandatory insurance" in ref
        assert "is not a TAN" in ref
        # The TAEG is not among the TAN-comparable values listed.
        listed = re.findall(r"<li>.*?</li>", ref, re.S)
        assert not any("3.81" in li for li in listed)

    def test_they_are_labelled_as_averages_not_an_offer(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "not an offer" in body[body.index('id="cost-rate-ref"') :][:2500]

    def test_the_values_come_from_rates_not_the_template(self, client, monkeypatch):
        from dataclasses import replace

        monkeypatch.setattr(
            rates,
            "MORTGAGE_RATE_AVERAGE",
            replace(rates.MORTGAGE_RATE_AVERAGE, low=3.11, central=3.11, high=3.11),
        )
        body = client.get(EN_URL).text
        assert "3.11%" in body[body.index('id="cost-rate-ref"') :][:2500]

    def test_italian_formats(self, client: TestClient):
        body = client.get(IT_URL).text
        ref = body[body.index('id="cost-rate-ref"') :]
        ref = ref[: ref.index("</div>")]
        assert "3,40%" in ref and "2,99%" in ref and "3,56%" in ref and "3,81%" in ref
        assert "luglio 2026" in ref

    def test_the_source_is_on_the_calculators_list_of_sources(self):
        urls = {url for _title, url in costs_view.SOURCES}
        assert rates.MORTGAGE_RATE_SOURCE_URL in urls

    @pytest.mark.parametrize("path", [IT_URL, EN_URL])
    def test_it_is_in_date_until_its_review_and_flagged_after(self, client, monkeypatch, path):
        due = rates.MORTGAGE_RATE_AVERAGE.due  # 2027-01-09
        monkeypatch.setattr(app_module, "_today", lambda: due)
        assert "ref-stale" not in client.get(path).text
        monkeypatch.setattr(app_module, "_today", lambda: due + timedelta(days=1))
        body = client.get(path).text
        ref = body[body.index('id="cost-rate-ref"') :][:600]
        assert 'class="tag stale ref-stale"' in ref

    def test_a_stale_reference_does_not_count_as_a_stale_line(self, client, monkeypatch):
        monkeypatch.setattr(
            app_module, "_today", lambda: rates.MORTGAGE_RATE_AVERAGE.due + timedelta(days=1)
        )
        body = client.get(EN_URL).text
        assert BADGE not in body and "ranges_overdue" not in body

    def test_the_methodology_explains_the_sources_and_the_split(self, client: TestClient):
        body = client.get(EN_URL).text
        assert "P × i ÷ (1 − (1 + i)^−n)" in body
        assert "does not publish a fixed/variable split" in body
        assert "cannot stand in for a TAN" in body

    def test_the_reference_is_in_the_label_context_for_a_result_page(self, estimate_env, client):
        body = client.post("/en/estimate", data=_form()).text
        assert "Market averages" in _costs_section(body)
