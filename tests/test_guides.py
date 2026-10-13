"""Editorial guides: the Markdown loader and its rules (data/guides.py), the renderer
(web/guide_view.py), the `/it/guide/{slug}` route and its SEO treatment, the calculator and
result-page links, and scripts/check_guides.py.

Every guide here is synthetic text written to `tmp_path`: the real ones live in a private
repository and never enter this one. STIMMO_CONTENT_DIR is pinned at `tmp_path` and the caches
are cleared on every write and on teardown, the pattern the neighborhood-blurb tests use.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import date
from html import unescape
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from stimmo.data import amenities, geocode, guides
from stimmo.data import neighborhoods as nb_module
from stimmo.data.guides import GuideError, GuideErrorCode, parse_guide, read_guide
from stimmo.i18n import gettext, use_locale
from stimmo.models import AmenityScore
from stimmo.web import app as app_module
from stimmo.web import costs_view, guide_view
from stimmo.web.app import _sitemap_xml, app
from stimmo.web.share_store import SqliteShareStore

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_guides.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_guides", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_guides = _load_script()

AI_NOTICE = (
    "This guide was written with significant help from an AI language model. "
    "We checked the sources it cites as carefully as we could, but inaccuracies may remain: "
    "confirm anything that matters with your notary, tax adviser or bank, and tell us if you "
    "find an error."
)
AI_NOTICE_CLASS = "guide-ai-note"
AI_SOURCE_TYPE = "https://schema.org/CompositeWithTrainedAlgorithmicMediaDigitalSource"

IT_CALCULATOR = "/it/costi-acquisto-casa-milano"
EN_CALCULATOR = "/en/milan/home-buying-costs"
COSTS_SLUG = costs_view.COSTS_GUIDE_SLUG
COSTS_GUIDE_PATH = f"/it/guide/{COSTS_SLUG}"
TODAY = "2026-10-11"

DESCRIPTION = (
    "Una descrizione di prova, abbastanza lunga da rispettare il minimo di cinquanta caratteri."
)
BODY = "Un paragrafo di prova.\n"


def guide_text(*, body: str = BODY, drop: tuple[str, ...] = (), extra: tuple[str, ...] = (), **raw):
    """A guide file. Front-matter values are written as given (quotes included), so a test can
    break one on purpose; `drop` removes a default key and `extra` appends raw lines."""
    fields = {
        "title": '"Titolo di prova"',
        "description": f'"{DESCRIPTION}"',
        "updated": "2026-10-01",
        **raw,
    }
    lines = ["---"]
    lines += [f"{key}: {value}" for key, value in fields.items() if key not in drop]
    lines += [*extra, "---", ""]
    return "\n".join(lines) + body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Content:
    """The pinned content directory: write guides (and blurbs) into it, caches kept honest."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.guides = root / "guides"

    @staticmethod
    def reset() -> None:
        guides.clear_cache()
        nb_module._neighborhoods_with_content.cache_clear()
        _sitemap_xml.cache_clear()

    def write(self, slug: str, text: str | None = None, **kwargs) -> Path:
        return self.write_file(f"{slug}.md", guide_text(**kwargs) if text is None else text)

    def write_file(self, filename: str, text: str) -> Path:
        self.guides.mkdir(exist_ok=True)
        path = self.guides / filename
        path.write_text(text, encoding="utf-8")
        self.reset()
        return path

    def neighborhoods(self, mapping: dict) -> None:
        (self.root / "neighborhoods.json").write_text(json.dumps(mapping), encoding="utf-8")
        self.reset()


@pytest.fixture()
def content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STIMMO_CONTENT_DIR", str(tmp_path))
    Content.reset()
    yield Content(tmp_path)
    Content.reset()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def estimate_env(monkeypatch: pytest.MonkeyPatch):
    """Nominatim/Overpass stubbed and a throwaway share store, so POST /estimate is hermetic."""
    monkeypatch.setattr(geocode, "geocode", lambda addr, *, city="Milano": (45.4642, 9.1900))
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: AmenityScore())
    monkeypatch.setattr(app_module, "_share_store", SqliteShareStore(":memory:"))


def _estimate_form() -> dict[str, str]:
    return {
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


def _ld_blocks(html: str) -> dict[str, dict]:
    """The page's JSON-LD nodes, by `@type`."""
    found = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    nodes = [json.loads(block) for block in found]
    return {node["@type"]: node for node in nodes}


def _italian_missing(msgid: str) -> bool:
    with use_locale("it_IT"):
        return gettext(msgid) == msgid


def needs_italian(msgid: str):
    """Skip an assertion about Italian *wording* until the catalog has been filled in by
    scripts/translate_po.py (msgstr is never hand-written)."""
    return pytest.mark.skipif(
        _italian_missing(msgid),
        reason=f"it_IT has no translation for {msgid!r} yet: run scripts/translate_po.py",
    )


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_a_complete_file(self):
        g = parse_guide(
            guide_text(h1='"Intestazione"', published="2026-09-01", body="Uno.\n\n## Due\n"),
            "una-guida",
        )
        assert (g.slug, g.title, g.h1) == ("una-guida", "Titolo di prova", "Intestazione")
        assert g.description == DESCRIPTION
        assert (g.published, g.updated) == (date(2026, 9, 1), date(2026, 10, 1))
        assert g.body_md == "Uno.\n\n## Due"

    def test_h1_defaults_to_title_and_published_to_updated(self):
        g = parse_guide(guide_text(), "una-guida")
        assert g.h1 == g.title
        assert g.published == g.updated == date(2026, 10, 1)

    def test_ai_assisted_defaults_to_true(self):
        assert parse_guide(guide_text(), "una-guida").ai_assisted is True

    @pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
    def test_ai_assisted_takes_a_boolean(self, value: str, expected: bool):
        assert parse_guide(guide_text(ai_assisted=value), "g").ai_assisted is expected

    @pytest.mark.parametrize(
        "value", ["yes", "no", "True", "FALSE", "1", "0", "on", "maybe", '"si"', "false false"]
    )
    def test_ai_assisted_rejects_anything_but_true_and_false(self, value: str):
        with pytest.raises(GuideError) as exc:
            parse_guide(guide_text(ai_assisted=value), "g")
        assert (exc.value.code, exc.value.line) == (GuideErrorCode.BAD_BOOLEAN, 5)

    def test_ai_assisted_is_not_an_unknown_key_and_cannot_repeat(self):
        parse_guide(guide_text(extra=("ai_assisted: false",)), "g")
        with pytest.raises(GuideError) as exc:
            parse_guide(guide_text(ai_assisted="true", extra=("ai_assisted: false",)), "g")
        assert exc.value.code == GuideErrorCode.DUPLICATE_KEY

    def test_one_pair_of_quotes_is_stripped_and_unquoted_values_work(self):
        g = parse_guide(guide_text(title="Senza virgolette: due punti"), "una-guida")
        assert g.title == "Senza virgolette: due punti"
        g = parse_guide(guide_text(title='"Con: due punti"'), "una-guida")
        assert g.title == "Con: due punti"
        # A quote that closes a value without having opened it is part of the value.
        g = parse_guide(guide_text(title='Il "prezzo-valore"'), "una-guida")
        assert g.title == 'Il "prezzo-valore"'

    def test_crlf_line_endings(self):
        g = parse_guide(guide_text().replace("\n", "\r\n"), "una-guida")
        assert g.title == "Titolo di prova"
        assert g.body_md == "Un paragrafo di prova."

    def test_body_line_is_where_the_body_starts(self):
        text = guide_text(body="\n\nPrimo paragrafo.\n")
        g = parse_guide(text, "una-guida")
        assert text.split("\n")[g.body_line - 1] == "Primo paragrafo."

    def test_published_equal_to_updated_is_fine(self):
        assert parse_guide(guide_text(published="2026-10-01"), "una-guida")

    def test_boundary_lengths(self):
        assert parse_guide(guide_text(title='"' + "x" * 70 + '"'), "g").title == "x" * 70
        assert parse_guide(guide_text(description='"' + "x" * 50 + '"'), "g")
        assert parse_guide(guide_text(description='"' + "x" * 170 + '"'), "g")

    def test_a_hash_that_is_not_a_heading_is_not_an_h1(self):
        parse_guide(guide_text(body="Un #hashtag e C# nel testo.\n\n#senzaspazio\n"), "g")

    def test_a_hash_inside_a_code_fence_or_indented_code_is_not_an_h1(self):
        parse_guide(guide_text(body="```\n# commento\n```\n\n~~~sh\n# altro\n~~~\n"), "g")
        parse_guide(guide_text(body="Testo.\n\n    # codice indentato\n"), "g")

    def test_h2_and_h3_are_fine(self):
        parse_guide(guide_text(body="## Due\n\n### Tre\n\n#### Quattro\n"), "g")

    def test_an_underlined_h2_followed_by_equals_is_not_a_setext_h1_after_an_atx_h2(self):
        parse_guide(guide_text(body="## Due\n===\n"), "g")

    @pytest.mark.parametrize(
        "body", ["Intro.\n\n# Titolo\n", "# Titolo\n", "Titolo\n======\n", "Intro.\n\n#\n"]
    )
    def test_an_h1_in_the_body_is_an_error(self, body: str):
        with pytest.raises(GuideError) as exc:
            parse_guide(guide_text(body=body), "g")
        assert exc.value.code == GuideErrorCode.H1_IN_BODY

    def test_h1_error_names_the_line_of_the_heading(self):
        text = guide_text(body="Intro.\n\n# Titolo\n")
        with pytest.raises(GuideError) as exc:
            parse_guide(text, "g")
        assert text.split("\n")[exc.value.line - 1] == "# Titolo"
        setext = guide_text(body="Intro.\n\nTitolo\n======\n")
        with pytest.raises(GuideError) as exc:
            parse_guide(setext, "g")
        assert setext.split("\n")[exc.value.line - 1] == "Titolo"

    def test_an_h1_after_a_closed_fence_is_still_caught(self):
        with pytest.raises(GuideError) as exc:
            parse_guide(guide_text(body="```\n# commento\n```\n\n# Titolo\n"), "g")
        assert exc.value.code == GuideErrorCode.H1_IN_BODY

    def test_the_slug_is_checked_too(self):
        for bad in ("Maiuscole", "con_underscore", "-inizio", "fine-", "due--trattini", ""):
            with pytest.raises(GuideError) as exc:
                parse_guide(guide_text(), bad)
            assert exc.value.code == GuideErrorCode.BAD_SLUG


# Each case is one rule: (the file text, the code it must raise, the line it must name).
_BROKEN: dict[str, tuple[str, GuideErrorCode, int]] = {
    "missing_title": (guide_text(drop=("title",)), GuideErrorCode.MISSING_TITLE, 1),
    "missing_description": (
        guide_text(drop=("description",)),
        GuideErrorCode.MISSING_DESCRIPTION,
        1,
    ),
    "missing_updated": (guide_text(drop=("updated",)), GuideErrorCode.MISSING_UPDATED, 1),
    "unknown_key": (guide_text(extra=("updatd: 2026-10-01",)), GuideErrorCode.UNKNOWN_KEY, 5),
    "duplicate_key": (guide_text(extra=("title: x",)), GuideErrorCode.DUPLICATE_KEY, 5),
    "bad_date": (guide_text(updated="2026-13-45"), GuideErrorCode.BAD_DATE, 4),
    "compact_date": (guide_text(updated="20261001"), GuideErrorCode.BAD_DATE, 4),
    "published_after_updated": (
        guide_text(published="2026-10-02"),
        GuideErrorCode.PUBLISHED_AFTER_UPDATED,
        5,
    ),
    "bad_boolean": (guide_text(ai_assisted="yes"), GuideErrorCode.BAD_BOOLEAN, 5),
    "h1_in_body": (guide_text(body="Intro.\n\n# Titolo\n"), GuideErrorCode.H1_IN_BODY, 8),
    "text_above_front_matter": (
        "NOTE\n\nqualche appunto\n\n" + guide_text(),
        GuideErrorCode.NO_FRONT_MATTER,
        1,
    ),
    "empty_file": ("", GuideErrorCode.NO_FRONT_MATTER, 1),
    "unterminated": ("---\ntitle: x\n", GuideErrorCode.UNTERMINATED_FRONT_MATTER, 1),
    "not_key_value": (
        guide_text(extra=("not a key value line",)),
        GuideErrorCode.BAD_FRONT_MATTER_LINE,
        5,
    ),
    "continuation_line": (
        guide_text(extra=("  continued",)),
        GuideErrorCode.BAD_FRONT_MATTER_LINE,
        5,
    ),
    "empty_value": (guide_text(extra=("h1:",)), GuideErrorCode.EMPTY_VALUE, 5),
    "unbalanced_quote": (guide_text(title='"aperto'), GuideErrorCode.UNBALANCED_QUOTE, 2),
    "title_too_long": (
        guide_text(title='"' + "x" * 71 + '"'),
        GuideErrorCode.BAD_TITLE_LENGTH,
        2,
    ),
    "description_too_short": (
        guide_text(description='"corta"'),
        GuideErrorCode.BAD_DESCRIPTION_LENGTH,
        3,
    ),
    "description_too_long": (
        guide_text(description='"' + "x" * 171 + '"'),
        GuideErrorCode.BAD_DESCRIPTION_LENGTH,
        3,
    ),
    "empty_body": (guide_text(body="\n\n"), GuideErrorCode.EMPTY_BODY, 5),
}


class TestParserErrors:
    @pytest.mark.parametrize("name", _BROKEN)
    def test_each_rule_raises_its_own_code_and_line(self, name: str):
        text, code, line = _BROKEN[name]
        with pytest.raises(GuideError) as exc:
            parse_guide(text, "g")
        assert (exc.value.code, exc.value.line) == (code, line)

    def test_the_content_rules_have_distinct_codes(self):
        listed = (
            "missing_title",
            "missing_description",
            "missing_updated",
            "unknown_key",
            "bad_date",
            "published_after_updated",
            "bad_boolean",
            "h1_in_body",
            "text_above_front_matter",
        )
        codes = {_BROKEN[name][1] for name in listed} | {GuideErrorCode.BAD_FILE_NAME}
        assert len(codes) == len(listed) + 1

    def test_the_message_never_contains_source_text(self):
        secret = "frase-riservata-da-non-stampare"
        text = guide_text(body=f"{secret}\n\n# {secret}\n", extra=(f"x {secret}",))
        with pytest.raises(GuideError) as exc:
            parse_guide(text, "g")
        assert secret not in str(exc.value)
        text = guide_text(body=f"{secret}\n\n# {secret}\n")
        with pytest.raises(GuideError) as exc:
            parse_guide(text, "g")
        assert secret not in str(exc.value)

    def test_str_is_the_code_and_line(self):
        with pytest.raises(GuideError) as exc:
            parse_guide(guide_text(updated="x"), "g")
        assert str(exc.value) == "BAD_DATE (line 4)"


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_no_directory_means_no_guides(self, content: Content):
        assert not content.guides.exists()
        assert guides.list_guides() == ()
        assert guides.guide_for_slug("x") is None

    def test_an_empty_directory_means_no_guides(self, content: Content):
        content.guides.mkdir()
        (content.guides / ".gitkeep").write_text("")
        content.reset()
        assert guides.list_guides() == ()

    def test_guides_are_sorted_by_slug_and_found_by_slug(self, content: Content):
        for slug in ("zeta", "alfa", "meta"):
            content.write(slug)
        assert [g.slug for g in guides.list_guides()] == ["alfa", "meta", "zeta"]
        assert guides.guide_for_slug("meta").slug == "meta"
        assert guides.guide_for_slug("nope") is None

    def test_gitkeep_is_ignored_next_to_guides(self, content: Content):
        content.write("alfa")
        (content.guides / ".gitkeep").write_text("")
        content.reset()
        assert [g.slug for g in guides.list_guides()] == ["alfa"]

    @pytest.mark.parametrize(
        "filename", ["Alfa.md", "alfa.txt", "alfa.markdown", "alfa", "un_guide.md", ".DS_Store"]
    )
    def test_a_file_that_is_not_slug_dot_md_raises(self, content: Content, filename: str):
        content.write_file(filename, guide_text())
        with pytest.raises(GuideError) as exc:
            guides.list_guides()
        assert exc.value.code == GuideErrorCode.BAD_FILE_NAME
        assert filename in str(exc.value)

    def test_a_directory_named_like_a_guide_raises(self, content: Content):
        (content.guides / "alfa.md").mkdir(parents=True)
        content.reset()
        with pytest.raises(GuideError) as exc:
            guides.list_guides()
        assert exc.value.code == GuideErrorCode.BAD_FILE_NAME

    def test_a_malformed_file_raises_and_names_the_file(self, content: Content):
        content.write("alfa")
        content.write("beta", guide_text(updated="ieri"))
        with pytest.raises(GuideError) as exc:
            guides.list_guides()
        assert (exc.value.code, exc.value.file) == (GuideErrorCode.BAD_DATE, "beta.md")
        assert str(exc.value) == "beta.md: BAD_DATE (line 4)"

    def test_a_file_that_is_not_utf8_raises(self, content: Content):
        content.guides.mkdir()
        (content.guides / "alfa.md").write_bytes(b"---\ntitle: \xff\xfe\n---\n")
        content.reset()
        with pytest.raises(GuideError) as exc:
            guides.list_guides()
        assert exc.value.code == GuideErrorCode.BAD_ENCODING

    def test_loading_is_cached_until_cleared(self, content: Content):
        content.write("alfa")
        assert len(guides.list_guides()) == 1
        (content.guides / "beta.md").write_text(guide_text(), encoding="utf-8")
        assert len(guides.list_guides()) == 1  # a restart (or a clear) picks it up
        guides.clear_cache()
        assert len(guides.list_guides()) == 2

    def test_a_slug_is_only_looked_up_never_used_as_a_path(self, content: Content):
        content.write("alfa")
        for hostile in ("../alfa", "..", "alfa/../alfa", "/etc/passwd", "alfa.md", ""):
            assert guides.guide_for_slug(hostile) is None


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


class TestRoute:
    def test_404_without_a_file_and_200_with_one(self, client: TestClient, content: Content):
        assert client.get("/it/guide/x").status_code == 404
        content.write("x", h1='"Intestazione di prova"')
        response = client.get("/it/guide/x")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<h1>Intestazione di prova</h1>" in response.text
        assert '<article class="guide-body">' in response.text

    def test_a_missing_file_is_a_plain_404_not_a_placeholder(
        self, client: TestClient, content: Content
    ):
        response = client.get("/it/guide/x")
        assert response.status_code == 404
        assert "<html" not in response.text
        assert "noindex" not in response.text

    def test_there_is_no_english_page_and_no_index(self, client: TestClient, content: Content):
        content.write("x")
        for path in ("/en/guide/x", "/it/guide", "/it/guide/", "/en/guide", "/en/guide/"):
            assert client.get(path).status_code == 404, path

    @pytest.mark.parametrize("slug", ["X", "not_a_slug", "-x", "x-", "x..y", "x%2Fy", "a b"])
    def test_a_bad_slug_is_404(self, client: TestClient, content: Content, slug: str):
        content.write("x")
        assert client.get(f"/it/guide/{slug}").status_code == 404

    def test_head_is_supported(self, client: TestClient, content: Content):
        content.write("x")
        assert client.head("/it/guide/x").status_code == 200
        assert client.head("/it/guide/y").status_code == 404

    def test_title_and_social_meta(self, client: TestClient, content: Content):
        content.write("x", title='"Una guida: cosa sapere"')
        html = client.get("/it/guide/x").text
        title = "Una guida: cosa sapere — stimmo"
        assert f"<title>{title}</title>" in html
        assert f'<meta property="og:title" content="{title}">' in html
        assert f'<meta name="description" content="{DESCRIPTION}">' in html
        assert f'<meta property="og:description" content="{DESCRIPTION}">' in html
        assert '<meta property="og:type" content="article">' in html
        assert '<meta property="og:url" content="http://testserver/it/guide/x">' in html
        assert '<meta property="og:locale" content="it_IT">' in html
        assert html.count('<meta name="description"') == 1

    def test_title_and_description_are_escaped(self, client: TestClient, content: Content):
        content.write(
            "x",
            title='"<b>grassetto</b> & altro"',
            description=f'"{DESCRIPTION} <i>x</i>"',
        )
        html = client.get("/it/guide/x").text
        assert "<b>grassetto</b>" not in html
        assert "&lt;b&gt;grassetto&lt;/b&gt;" in html
        assert "<i>x</i>" not in html

    def test_the_updated_date_and_author_line(self, client: TestClient, content: Content):
        content.write("x", updated="2026-10-01")
        html = client.get("/it/guide/x").text
        assert '<time datetime="2026-10-01">1 ottobre 2026</time>' in html
        assert 'href="https://github.com/alediaferia"' in html

    @needs_italian("Updated on %(date)s")
    def test_the_updated_line_is_in_italian(self, client: TestClient, content: Content):
        content.write("x", updated="2026-10-01")
        with use_locale("it_IT"):
            lead = gettext("Updated on %(date)s").split("%(date)s")[0]
        assert lead != "Updated on " and f"{lead}<time" in client.get("/it/guide/x").text

    def test_the_stylesheet_is_linked(self, client: TestClient, content: Content):
        content.write("x")
        assert re.search(
            r'<link rel="stylesheet" href="/static/guide\.css\?v=\w+">',
            client.get("/it/guide/x").text,
        )
        assert client.get("/static/guide.css").status_code == 200

    def test_the_correction_note_is_on_every_guide(self, client: TestClient, content: Content):
        for slug in ("alfa", "beta"):
            content.write(slug)
            html = client.get(f"/it/guide/{slug}").text
            expected = (
                "https://github.com/alediaferia/stimmo/issues/new"
                f"?template=correzione-contenuti.yml&amp;title=[Correzione]%20{slug}"
            )
            assert expected in html
            assert html.index("guide-body") < html.index(expected)  # after the body

    def test_the_ai_notice_is_shown_by_default(self, client: TestClient, content: Content):
        content.write("x")
        html = client.get("/it/guide/x").text
        assert html.count(f'<p class="{AI_NOTICE_CLASS}">') == 1
        # In the same callout as the correction note, after the body.
        callout = html[html.index('<div class="note guide-note">') :]
        assert "correzione-contenuti" in callout and AI_NOTICE_CLASS in callout
        assert html.index("guide-body") < html.index(AI_NOTICE_CLASS)

    def test_the_ai_notice_is_shown_with_an_explicit_true(
        self, client: TestClient, content: Content
    ):
        content.write("x", ai_assisted="true")
        assert AI_NOTICE_CLASS in client.get("/it/guide/x").text

    def test_the_ai_notice_is_hidden_when_the_guide_says_so(
        self, client: TestClient, content: Content
    ):
        content.write("x", ai_assisted="false")
        response = client.get("/it/guide/x")
        assert response.status_code == 200
        html = unescape(response.text)
        assert AI_NOTICE_CLASS not in html
        with use_locale("it_IT"):
            assert gettext(AI_NOTICE) not in html
        assert AI_NOTICE not in html
        # The correction note is unaffected.
        assert "correzione-contenuti" in html

    def test_the_ai_notice_is_per_guide(self, client: TestClient, content: Content):
        content.write("ai")
        content.write("human", ai_assisted="false")
        assert AI_NOTICE_CLASS in client.get("/it/guide/ai").text
        assert AI_NOTICE_CLASS not in client.get("/it/guide/human").text

    def test_a_guide_with_a_bad_ai_assisted_value_is_not_served(
        self, client: TestClient, content: Content
    ):
        content.write("x", ai_assisted="yes")
        with pytest.raises(GuideError) as exc:
            client.get("/it/guide/x")
        assert exc.value.code == GuideErrorCode.BAD_BOOLEAN

    def test_the_ai_notice_is_in_italian(self, client: TestClient, content: Content):
        content.write("x")
        with use_locale("it_IT"):
            italian = gettext(AI_NOTICE)
        assert italian != AI_NOTICE, "run scripts/translate_po.py: it_IT has no translation"
        html = unescape(client.get("/it/guide/x").text)
        assert italian in html
        assert AI_NOTICE not in html

    def test_the_correction_note_is_not_in_the_markdown(self, content: Content):
        # The note is the template's: a guide's own text cannot be what carries it.
        assert "correzione-contenuti" not in guide_text()

    @needs_italian("Open an issue on GitHub")
    def test_the_correction_note_is_in_italian(self, client: TestClient, content: Content):
        content.write("x")
        html = client.get("/it/guide/x").text
        with use_locale("it_IT"):
            expected = [
                gettext("Found an error, or want something added?"),
                gettext("Open an issue on GitHub"),
            ]
        assert all(text in html for text in expected)


# ---------------------------------------------------------------------------
# SEO
# ---------------------------------------------------------------------------


def _alternates(html: str) -> dict[str, str]:
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', html))


def _canonical(html: str) -> str:
    return re.search(r'<link rel="canonical" href="([^"]+)">', html).group(1)


def _url_block(sitemap: str, loc: str) -> str:
    match = re.search(rf"<url>\s*<loc>{re.escape(loc)}</loc>(.*?)</url>", sitemap, re.S)
    assert match, loc
    return match.group(1)


class TestSeo:
    def test_italian_only_alternates_and_a_self_canonical(
        self, client: TestClient, content: Content
    ):
        content.write("x")
        html = client.get("/it/guide/x").text
        canonical = _canonical(html)
        assert canonical == "http://testserver/it/guide/x"
        assert _alternates(html) == {"it": canonical, "x-default": canonical}
        # Nothing on the page, head or language switcher, points at an English guide URL.
        assert "/en/guide" not in html

    def test_the_sitemap_lists_the_guide_iff_the_file_exists(
        self, client: TestClient, content: Content
    ):
        assert "/guide/" not in client.get("/sitemap.xml").text
        content.write("x", updated="2026-10-01", published="2026-09-01")
        body = client.get("/sitemap.xml").text
        locs = re.findall(r"<loc>(.*?)</loc>", body)
        assert [loc for loc in locs if "/guide/" in loc] == ["https://stimmo.it/it/guide/x"]
        block = _url_block(body, "https://stimmo.it/it/guide/x")
        assert "<lastmod>2026-10-01</lastmod>" in block  # `updated`, not `published`
        assert re.findall(r'hreflang="([^"]+)"', block) == ["it", "x-default"]
        assert "/en/guide/" not in body

    def test_removing_the_file_removes_the_url(self, client: TestClient, content: Content):
        path = content.write("x")
        assert "/it/guide/x" in client.get("/sitemap.xml").text
        path.unlink()
        content.reset()
        assert "/it/guide/x" not in client.get("/sitemap.xml").text

    def test_every_guide_is_listed_with_its_own_lastmod(self, client: TestClient, content: Content):
        content.write("alfa", updated="2026-10-01")
        content.write("beta", updated="2026-10-05")
        body = client.get("/sitemap.xml").text
        assert "<lastmod>2026-10-01</lastmod>" in _url_block(
            body, "https://stimmo.it/it/guide/alfa"
        )
        assert "<lastmod>2026-10-05</lastmod>" in _url_block(
            body, "https://stimmo.it/it/guide/beta"
        )

    def test_the_registry_entry_is_italian_only(self):
        route = app_module._SEO_ROUTES["guide_detail"]
        assert dict(route.suffixes) == {"it": "/guide/{slug}"}
        assert route.endpoint == "guide_detail"

    def test_public_clone_has_no_guides(self, client: TestClient, content: Content):
        """With no content directory at all: no sitemap URL and every guide path is a 404."""
        assert not content.guides.exists()
        assert "/it/guide/" not in client.get("/sitemap.xml").text
        for slug in ("x", COSTS_SLUG, guides.PILLAR_SLUG):
            assert client.get(f"/it/guide/{slug}").status_code == 404
        assert guides.list_guides() == ()
        assert app_module.guide_url(COSTS_SLUG) is None
        assert app_module.costs_guide_link("it") is None


# ---------------------------------------------------------------------------
# Rendering and link gating
# ---------------------------------------------------------------------------


def _render(body: str, *others: str) -> str:
    """The body as HTML, with `others` as the slugs of the guides that exist."""
    g = parse_guide(guide_text(body=body), "g")
    return str(guide_view._render(g, frozenset(others)))


class TestRendering:
    def test_a_script_line_renders_as_text(self, client: TestClient, content: Content):
        content.write("x", body="<script>alert(1)</script>\n\nDopo.\n")
        html = client.get("/it/guide/x").text
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_raw_html_is_escaped_block_and_inline(self, client: TestClient, content: Content):
        content.write(
            "x",
            body='<div onclick="x()">blocco</div>\n\nUn <span onmouseover="y()">inline</span>.\n',
        )
        html = client.get("/it/guide/x").text
        assert "<div onclick" not in html and "<span onmouseover" not in html
        assert "&lt;div onclick=&quot;x()&quot;&gt;blocco&lt;/div&gt;" in html
        assert "&lt;span onmouseover=" in html

    @pytest.mark.parametrize(
        "href",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "http://example.com/a",
            "data:text/html,hi",
            "vbscript:x",
            "mailto:a@b.it",
            "//example.com/x",
            "relativo/percorso",
            "../su",
            "/en/about",
            "/about",
            "/it/../en/about",
            "/it/./x",
            "/it",
            "/itx/ciao",
            "ftp://example.com/x",
        ],
    )
    def test_a_link_that_is_not_allowed_keeps_its_text_and_loses_its_href(self, href: str):
        html = _render(f"Prima [parola chiave]({href}) dopo.\n")
        assert "<a" not in html and "href" not in html
        assert "Prima parola chiave dopo." in html

    def test_the_documented_issue_examples(self, client: TestClient, content: Content):
        content.write("x", body="[x](javascript:alert(1)) e [y](http://example.com/a)\n")
        html = client.get("/it/guide/x").text
        assert "javascript:" not in html
        assert 'href="http://example.com' not in html
        assert "<p>x e y</p>" in html

    @pytest.mark.parametrize(
        "href",
        [
            "/it/",
            "/it/zones",
            "/it/zones/B15",
            "/it/costi-acquisto-casa-milano#not-included",
            "/it/costi-acquisto-casa-milano?price=1&x=2",
            "#sezione",
            "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:1985-02-27;52~art29",
            "https://example.com",
        ],
    )
    def test_a_link_that_is_allowed_is_an_anchor(self, href: str):
        html = _render(f"Vai [qui]({href}) ora.\n")
        assert re.search(r'<a href="[^"]+">qui</a>', html)

    def test_an_autolink_follows_the_same_rule(self):
        assert "<a" not in _render("<http://example.com>\n")
        assert '<a href="https://example.com">' in _render("<https://example.com>\n")

    def test_a_reference_style_link_follows_the_same_rule(self):
        html = _render("[uno][a] e [due][b]\n\n[a]: javascript:alert(1)\n[b]: /it/zones\n")
        assert html.count("<a") == 1 and 'href="/it/zones"' in html

    def test_an_image_is_only_its_alt_text(self):
        html = _render("![descrizione](https://example.com/a.png) e ![](/it/x.png)\n")
        assert "<img" not in html and "example.com" not in html
        assert "descrizione" in html

    def test_a_link_to_a_missing_guide_is_text_until_the_file_exists(
        self, client: TestClient, content: Content
    ):
        content.write(
            "x", body="Leggi [la guida](/it/guide/missing) e [ancora](/it/guide/missing#a).\n"
        )
        html = client.get("/it/guide/x").text
        assert "/it/guide/missing" not in html
        assert "Leggi la guida e ancora." in html
        content.write("missing")  # clears the caches
        html = client.get("/it/guide/x").text
        assert '<a href="/it/guide/missing">la guida</a>' in html
        assert '<a href="/it/guide/missing#a">ancora</a>' in html

    @pytest.mark.parametrize("href", ["/it/guide", "/it/guide/", "/it/guide/x/", "/it/guide/X"])
    def test_a_malformed_guide_path_is_text(self, href: str):
        html = _render(f"[t]({href})\n", "x", "X")
        assert "<a" not in html

    def test_a_link_to_a_guide_that_exists_is_an_anchor(self):
        assert '<a href="/it/guide/x">t</a>' in _render("[t](/it/guide/x)\n", "x")

    def test_a_table_is_wrapped_in_table_scroll(self, client: TestClient, content: Content):
        content.write("x", body="| Voce | Importo |\n|---|--:|\n| Notaio | 3.000 |\n")
        html = client.get("/it/guide/x").text
        assert re.search(r'<div class="table-scroll">\s*<table>.*?</table>\s*</div>', html, re.S)
        assert 'style="text-align:right"' in html
        assert html.count('<div class="table-scroll">') == 1

    def test_two_tables_are_two_wrappers(self):
        table = "| a |\n|---|\n| 1 |\n\n"
        html = _render(table + table)
        assert html.count('<div class="table-scroll">') == 2
        assert html.count("</table>") == 2

    def test_a_link_inside_a_table_cell_is_gated_too(self):
        html = _render("| a |\n|---|\n| [x](javascript:1) [y](/it/zones) |\n")
        assert "javascript" not in html and 'href="/it/zones"' in html

    def test_headings_get_stable_ids(self):
        html = _render(
            "## 1. Il registro, prima casa\n\n### L'esempio: voce per voce\n\n## Perché è così\n"
        )
        assert '<h2 id="1-il-registro-prima-casa">' in html
        assert '<h3 id="l-esempio-voce-per-voce">' in html
        assert '<h2 id="perche-e-cosi">' in html

    def test_repeated_headings_stay_unique_and_stable(self):
        body = "## Voce\n\n## Voce\n\n## Voce\n"
        html = _render(body)
        assert re.findall(r'id="([^"]+)"', html) == ["voce", "voce-2", "voce-3"]
        assert _render(body) == html

    def test_a_heading_made_of_symbols_still_gets_an_id(self):
        assert '<h2 id="section">' in _render("## ???\n")

    def test_inline_markup_in_a_heading_is_not_part_of_its_id(self):
        assert '<h2 id="costi-e-spese">' in _render("## *Costi* e [spese](/it/zones)\n")

    def test_an_anchor_link_reaches_a_heading(self, client: TestClient, content: Content):
        content.write("x", body="Vedi [il notaio](#il-notaio).\n\n## Il notaio\n")
        html = client.get("/it/guide/x").text
        assert 'href="#il-notaio"' in html and 'id="il-notaio"' in html

    def test_markdown_basics(self):
        html = _render(
            "Testo con **grassetto**, *corsivo* e `codice <b>`.\n\n"
            "- uno\n- due\n\n1. a\n2. b\n\n> citazione\n\n---\n\n"
            "```\n<x> & y\n```\n"
        )
        for fragment in (
            "<strong>grassetto</strong>",
            "<em>corsivo</em>",
            "<code>codice &lt;b&gt;</code>",
            "<ul>",
            "<ol>",
            "<blockquote>",
            "<hr />",
            "&lt;x&gt; &amp; y",
        ):
            assert fragment in html, fragment

    def test_typographer_and_linkify_are_off(self):
        html = _render('"dritte" -- e www.example.com e https://example.com/auto\n')
        assert "&quot;dritte&quot;" in html and "--" in html
        assert "<a" not in html

    def test_rendering_is_cached_per_guide_and_set_of_guides(self, content: Content):
        content.write("x", body="[l](/it/guide/y)\n")
        g = guides.guide_for_slug("x")
        first = guide_view.render_body(g)
        assert guide_view.render_body(g) is first
        content.write("y")
        assert 'href="/it/guide/y"' in guide_view.render_body(guides.guide_for_slug("x"))

    def test_the_body_is_markup(self, content: Content):
        content.write("x")
        from markupsafe import Markup

        assert isinstance(guide_view.render_body(guides.guide_for_slug("x")), Markup)

    def test_internal_links_lists_site_links_with_their_source_line(self):
        text = guide_text(
            body="Uno [a](/it/zones).\n\nDue [b](https://example.com) [c](/it/x#y).\n"
        )
        g = parse_guide(text, "g")
        lines = text.split("\n")
        found = guide_view.internal_links(g)
        assert [href for href, _ in found] == ["/it/zones", "/it/x#y"]
        assert "[a](/it/zones)" in lines[found[0][1] - 1]
        assert "[c](/it/x#y)" in lines[found[1][1] - 1]


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------


class TestJsonLd:
    def test_article(self, client: TestClient, content: Content):
        content.write(
            "x",
            title='"Titolo della guida"',
            h1='"Intestazione"',
            published="2026-09-01",
            updated="2026-10-01",
        )
        html = client.get("/it/guide/x").text
        article = _ld_blocks(html)["Article"]
        assert article["@context"] == "https://schema.org"
        assert article["headline"] == "Titolo della guida"
        assert len(article["headline"]) <= 110
        assert article["description"] == DESCRIPTION
        assert article["datePublished"] == "2026-09-01"
        assert article["dateModified"] == "2026-10-01"
        assert article["author"] == {
            "@type": "Person",
            "name": "Alessandro Diaferia",
            "url": "https://github.com/alediaferia",
        }
        assert article["publisher"] == {"@id": "http://testserver/#organization"}
        assert article["mainEntityOfPage"] == _canonical(html) == "http://testserver/it/guide/x"
        assert article["inLanguage"] == "it-IT"
        assert article["isAccessibleForFree"] is True

    def test_an_ai_assisted_guide_says_so_in_digital_source_type(
        self, client: TestClient, content: Content
    ):
        content.write("x")
        article = _ld_blocks(client.get("/it/guide/x").text)["Article"]
        assert article["digitalSourceType"] == AI_SOURCE_TYPE

    def test_a_human_guide_has_no_digital_source_type(self, client: TestClient, content: Content):
        content.write("x", ai_assisted="false")
        article = _ld_blocks(client.get("/it/guide/x").text)["Article"]
        assert "digitalSourceType" not in article

    def test_the_author_is_the_one_the_calculator_page_names(
        self, client: TestClient, content: Content
    ):
        content.write("x")
        article = _ld_blocks(client.get("/it/guide/x").text)["Article"]
        creator = _ld_blocks(client.get(IT_CALCULATOR).text)["WebApplication"]["creator"]
        assert article["author"] == creator

    def test_published_falls_back_to_updated(self, client: TestClient, content: Content):
        content.write("x", updated="2026-10-01")
        article = _ld_blocks(client.get("/it/guide/x").text)["Article"]
        assert article["datePublished"] == article["dateModified"] == "2026-10-01"

    def test_the_organization_node_the_publisher_points_at_is_on_the_page(
        self, client: TestClient, content: Content
    ):
        content.write("x")
        nodes = _ld_blocks(client.get("/it/guide/x").text)
        assert nodes["Organization"]["@id"] == nodes["Article"]["publisher"]["@id"]

    def test_there_is_no_faq_page(self, client: TestClient, content: Content):
        content.write("x", body="## Domande\n\n**Quanto costa?** Dipende.\n")
        html = client.get("/it/guide/x").text
        assert "FAQPage" not in html
        assert set(_ld_blocks(html)) == {"Organization", "Article", "BreadcrumbList"}

    def test_breadcrumb_without_the_pillar(self, client: TestClient, content: Content):
        content.write("x", h1='"Intestazione"')
        crumbs = _ld_blocks(client.get("/it/guide/x").text)["BreadcrumbList"]["itemListElement"]
        assert [(c["position"], c["name"], c["item"]) for c in crumbs] == [
            (1, "stimmo", "http://testserver/it/"),
            (2, "Intestazione", "http://testserver/it/guide/x"),
        ]

    def test_breadcrumb_includes_the_pillar_iff_its_file_exists(
        self, client: TestClient, content: Content
    ):
        content.write("x", h1='"Intestazione"')
        content.write(guides.PILLAR_SLUG, h1='"Il pilastro"')
        crumbs = _ld_blocks(client.get("/it/guide/x").text)["BreadcrumbList"]["itemListElement"]
        assert [(c["position"], c["name"], c["item"]) for c in crumbs] == [
            (1, "stimmo", "http://testserver/it/"),
            (2, "Il pilastro", f"http://testserver/it/guide/{guides.PILLAR_SLUG}"),
            (3, "Intestazione", "http://testserver/it/guide/x"),
        ]
        (content.guides / f"{guides.PILLAR_SLUG}.md").unlink()
        content.reset()
        crumbs = _ld_blocks(client.get("/it/guide/x").text)["BreadcrumbList"]["itemListElement"]
        assert len(crumbs) == 2

    def test_the_pillar_page_is_not_its_own_parent(self, client: TestClient, content: Content):
        content.write(guides.PILLAR_SLUG, h1='"Il pilastro"')
        crumbs = _ld_blocks(client.get(f"/it/guide/{guides.PILLAR_SLUG}").text)["BreadcrumbList"]
        assert [c["name"] for c in crumbs["itemListElement"]] == ["stimmo", "Il pilastro"]

    def test_json_ld_cannot_be_broken_out_of(self, client: TestClient, content: Content):
        content.write("x", title='"</script><script>alert(1)</script>"')
        html = client.get("/it/guide/x").text
        assert "</script><script>alert(1)" not in html
        assert _ld_blocks(html)["Article"]["headline"] == "</script><script>alert(1)</script>"


# ---------------------------------------------------------------------------
# The calculator and the result page link to the costs guide
# ---------------------------------------------------------------------------


class TestCostsGuideLinks:
    def test_the_italian_calculator_links_iff_the_file_exists(
        self, client: TestClient, content: Content
    ):
        assert COSTS_GUIDE_PATH not in client.get(IT_CALCULATOR).text
        content.write(COSTS_SLUG, h1='"Le voci nascoste di prova"')
        html = client.get(IT_CALCULATOR).text
        assert f'<a href="{COSTS_GUIDE_PATH}">Le voci nascoste di prova →</a>' in html
        # In the "Not included" section, outside the collapsed methodology.
        assert (
            html.index('id="not-included"')
            < html.index(COSTS_GUIDE_PATH)
            < html.index('id="method"')
        )

    def test_the_english_calculator_never_links(self, client: TestClient, content: Content):
        content.write(COSTS_SLUG)
        assert "/guide/" not in client.get(EN_CALCULATOR).text

    def test_a_per_visitor_variant_links_too(self, client: TestClient, content: Content):
        content.write(COSTS_SLUG)
        assert COSTS_GUIDE_PATH in client.get(f"{IT_CALCULATOR}?price=300000&surface=60").text

    def test_another_guide_does_not_trigger_the_link(self, client: TestClient, content: Content):
        content.write("altra-guida")
        assert "/guide/" not in client.get(IT_CALCULATOR).text

    def test_the_anchor_text_is_the_guides_own_h1_and_is_escaped(
        self, client: TestClient, content: Content
    ):
        content.write(COSTS_SLUG, h1='"<i>Voci</i> & costi"')
        html = client.get(IT_CALCULATOR).text
        assert "&lt;i&gt;Voci&lt;/i&gt; &amp; costi →" in html
        assert "<i>Voci</i>" not in html

    def test_the_italian_result_page_links_next_to_the_calculator_link(
        self, client: TestClient, content: Content, estimate_env
    ):
        before = client.post("/it/estimate", data=_estimate_form()).text
        assert COSTS_GUIDE_PATH not in before
        content.write(COSTS_SLUG, h1='"Le voci nascoste di prova"')
        html = client.post("/it/estimate", data=_estimate_form()).text
        costs = html[html.index('id="costs"') :]
        calculator_link = f'<a href="{IT_CALCULATOR}#method">'
        guide_link = f'<a href="{COSTS_GUIDE_PATH}">Le voci nascoste di prova →</a>'
        assert calculator_link in costs and guide_link in costs
        assert costs.index(calculator_link) < costs.index(guide_link)
        assert html.count(COSTS_GUIDE_PATH) == 1

    def test_the_english_result_page_never_links(
        self, client: TestClient, content: Content, estimate_env
    ):
        content.write(COSTS_SLUG)
        assert "/guide/" not in client.post("/en/estimate", data=_estimate_form()).text

    def test_the_share_view_follows_the_costs_section(
        self, client: TestClient, content: Content, estimate_env
    ):
        content.write(COSTS_SLUG)
        body = client.post("/it/estimate", data=_estimate_form()).text
        share_path = re.search(r'data-url="https?://[^/]+(/s/[A-Za-z0-9]+)"', body)
        assert share_path
        shared = client.get(share_path.group(1), headers={"Accept-Language": "it"})
        assert shared.status_code == 200 and 'id="costs"' in shared.text
        assert COSTS_GUIDE_PATH in shared.text
        english = client.get(share_path.group(1), headers={"Accept-Language": "en"})
        assert "/guide/" not in english.text

    def test_guide_url_and_the_link_helper(self, content: Content):
        assert app_module.guide_url("x") is None
        content.write("x")
        assert app_module.guide_url("x") == "/it/guide/x"
        assert app_module.costs_guide_link("it") is None
        content.write(COSTS_SLUG, h1='"Intestazione"')
        assert app_module.costs_guide_link("it") == {"url": COSTS_GUIDE_PATH, "h1": "Intestazione"}
        assert app_module.costs_guide_link("en") is None

    def test_the_calculator_lastmod_is_unchanged_by_a_guide(
        self, client: TestClient, content: Content
    ):
        def lastmod() -> str:
            body = client.get("/sitemap.xml").text
            return re.search(
                rf"<loc>https://stimmo.it{IT_CALCULATOR}</loc>\s*<lastmod>([^<]+)</lastmod>", body
            ).group(1)

        before = lastmod()
        content.write(COSTS_SLUG, updated="2026-10-10")
        assert lastmod() == before


# ---------------------------------------------------------------------------
# The language switcher on a page with no English alternate
# ---------------------------------------------------------------------------

# The switcher partial exactly as it was before it learned about IT-only pages. Every existing
# page must still render this byte for byte; only a page whose registry entry has no "en"
# suffix may differ.
_LEGACY_SWITCHER = """\
{% set _other = "en" if lang == "it" else "it" %}
{% set _cur_path = request.url.path %}
{% set _other_path = _cur_path | replace("/" + lang + "/", "/" + _other + "/") %}
<div class="lang-switcher">
  <form method="post" action="/set-lang" style="display:inline">
    <input type="hidden" name="lang" value="it">
    <input type="hidden" name="next" value="{{ "/" + "it" + "/" + _cur_path.split("/")[2:] | join("/") if _cur_path.startswith("/" + lang + "/") else "/it/" }}">
    <button type="submit" class="lang-pill{% if lang == 'it' %} active{% endif %}">IT</button>
  </form>
  <form method="post" action="/set-lang" style="display:inline">
    <input type="hidden" name="lang" value="en">
    <input type="hidden" name="next" value="{{ "/" + "en" + "/" + _cur_path.split("/")[2:] | join("/") if _cur_path.startswith("/" + lang + "/") else "/en/" }}">
    <button type="submit" class="lang-pill{% if lang == 'en' %} active{% endif %}">EN</button>
  </form>
</div>
"""  # noqa: E501


def _legacy_switcher(path: str) -> str:
    request = SimpleNamespace(url=SimpleNamespace(path=path))
    template = app_module.templates.env.from_string(_LEGACY_SWITCHER)
    return template.render(request=request, lang=path.split("/")[1])


def _next_values(html: str) -> list[str]:
    return re.findall(r'<input type="hidden" name="next" value="([^"]*)">', html)


class TestLanguageSwitcher:
    _PAGES = (
        "/it/",
        "/en/",
        "/it/about",
        "/en/about",
        "/it/privacy",
        "/it/bookmarklet",
        "/it/zones",
        "/en/zones",
        "/it/zones/B15",
        "/en/zones/B15",
        "/it/milano/brera-prezzi-al-mq",
        "/en/milan/brera-property-prices",
        IT_CALCULATOR,
        EN_CALCULATOR,
    )

    @pytest.mark.parametrize("path", _PAGES)
    def test_every_existing_page_renders_the_switcher_byte_for_byte_as_before(
        self, client: TestClient, content: Content, path: str
    ):
        # With guides present, so a leak of the guide logic into other pages would show.
        content.write("x")
        content.write(COSTS_SLUG)
        response = client.get(path)
        assert response.status_code == 200
        assert _legacy_switcher(path) in response.text

    def test_the_result_page_renders_it_byte_for_byte_as_before(
        self, client: TestClient, content: Content, estimate_env
    ):
        content.write("x")
        response = client.post("/it/estimate", data=_estimate_form())
        assert _legacy_switcher("/it/estimate") in response.text

    def test_an_error_page_renders_it_byte_for_byte_as_before(self, client: TestClient):
        response = client.get("/it/import?src=nope&p=x")
        assert response.status_code == 400
        assert _legacy_switcher("/it/import") in response.text

    def test_the_english_pill_on_a_guide_goes_to_the_english_home(
        self, client: TestClient, content: Content
    ):
        content.write("x")
        html = client.get("/it/guide/x").text
        # The Italian pill is the page itself; the English one is the English home page, not a 404.
        assert _next_values(html) == ["/it/guide/x", "/en/"]
        assert re.search(r'class="lang-pill active">IT</button>', html)
        assert "/en/guide" not in html

    def test_it_follows_the_registry_not_the_path(
        self, client: TestClient, content: Content, monkeypatch: pytest.MonkeyPatch
    ):
        # Give the guide route an English suffix and the pill follows the page again; nothing in
        # the switcher knows what a guide URL looks like.
        content.write("x")
        route = app_module._SEO_ROUTES["guide_detail"]
        both = app_module.SeoRoute(
            key=route.key,
            endpoint=route.endpoint,
            suffixes={"it": "/guide/{slug}", "en": "/guide/{slug}"},
            expand=route.expand,
            lastmod=route.lastmod,
        )
        monkeypatch.setitem(app_module._SEO_ROUTES, "guide_detail", both)
        assert _next_values(client.get("/it/guide/x").text) == ["/it/guide/x", "/en/guide/x"]


# ---------------------------------------------------------------------------
# Guard: no neighborhood page links to a guide
# ---------------------------------------------------------------------------


class TestNoHubLinksToAGuide:
    # Guard: no neighborhood page links to a guide yet. Remove this class deliberately, in the
    # change that adds links from the neighborhood pages.
    @pytest.mark.parametrize("n", nb_module.list_neighborhoods(), ids=lambda n: n.slug_en)
    def test_a_rendered_hub_has_no_guide_link(
        self, client: TestClient, content: Content, n: nb_module.Neighborhood
    ):
        content.write(COSTS_SLUG)
        content.write(guides.PILLAR_SLUG)
        content.neighborhoods(
            {n.slug_en: {"blurb_it": "Testo editoriale.", "blurb_en": "Editorial copy."}}
        )
        for url in (
            f"/it/milano/{n.slug_it}-prezzi-al-mq",
            f"/en/milan/{n.slug_en}-property-prices",
        ):
            response = client.get(url)
            assert response.status_code == 200, url
            assert "/guide/" not in response.text, url

    def test_an_example_hub(self, client: TestClient, content: Content):
        content.write(COSTS_SLUG)
        assert "/it/guide/" not in client.get("/it/milano/brera-prezzi-al-mq").text


# ---------------------------------------------------------------------------
# scripts/check_guides.py
# ---------------------------------------------------------------------------


def _check(capsys, directory: Path, *args: str) -> tuple[int, list[str]]:
    code = check_guides.main([str(directory), "--today", TODAY, *args])
    out = capsys.readouterr()
    return code, out.out.splitlines()


class TestCheckScript:
    def test_a_clean_directory_passes(self, capsys, content: Content):
        content.write(
            "alfa", body="Vedi [il calcolatore](/it/costi-acquisto-casa-milano#not-included).\n"
        )
        content.write("beta")
        assert _check(capsys, content.guides) == (0, ["check_guides: ok"])

    def test_an_empty_directory_passes(self, capsys, content: Content):
        content.guides.mkdir()
        (content.guides / ".gitkeep").write_text("")
        assert _check(capsys, content.guides)[0] == 0

    @pytest.mark.parametrize("name", _BROKEN)
    def test_every_parser_rule_fails_the_script_with_its_code(
        self, capsys, content: Content, name: str
    ):
        text, code, line = _BROKEN[name]
        content.write("alfa", text)
        status, out = _check(capsys, content.guides)
        assert status == 1
        assert out == [f"alfa.md: {code} (line {line})"]

    def test_a_bad_file_name_fails(self, capsys, content: Content):
        content.write_file("Bad_Name.md", guide_text())
        status, out = _check(capsys, content.guides)
        assert (status, out) == (1, ["Bad_Name.md: BAD_FILE_NAME"])

    def test_a_file_that_is_not_markdown_fails(self, capsys, content: Content):
        content.write_file("note.txt", "x")
        assert _check(capsys, content.guides) == (1, ["note.txt: BAD_FILE_NAME"])

    def test_every_broken_file_is_reported_not_only_the_first(self, capsys, content: Content):
        content.write("alfa", guide_text(updated="ieri"))
        content.write("beta")
        content.write("gamma", "senza front matter\n")
        status, out = _check(capsys, content.guides)
        assert status == 1
        assert out == ["alfa.md: BAD_DATE (line 4)", "gamma.md: NO_FRONT_MATTER (line 1)"]

    def test_a_file_with_text_above_front_matter_cannot_pass(self, capsys, content: Content):
        content.write("alfa", "NOTE\n\nqualche appunto\n\n" + guide_text())
        status, out = _check(capsys, content.guides)
        assert (status, out) == (1, ["alfa.md: NO_FRONT_MATTER (line 1)"])

    def test_updated_more_than_a_day_ahead_fails(self, capsys, content: Content):
        content.write("alfa", updated="2026-10-13")
        status, out = _check(capsys, content.guides)
        assert (status, out) == (1, ["alfa.md: UPDATED_IN_FUTURE (line 4)"])

    @pytest.mark.parametrize("updated", ["2026-10-11", "2026-10-12", "2026-01-01"])
    def test_updated_today_tomorrow_or_earlier_passes(self, capsys, content: Content, updated: str):
        content.write("alfa", updated=updated)
        assert _check(capsys, content.guides)[0] == 0

    def test_an_unknown_neighborhood_slug_fails(self, capsys, content: Content):
        content.write("alfa", body="Leggi [Brera](/it/milano/brera-nope-prezzi-al-mq).\n")
        status, out = _check(capsys, content.guides)
        assert (status, out) == (1, ["alfa.md: UNRESOLVED_LINK (line 6)"])

    def test_the_calculator_and_zones_pass(self, capsys, content: Content):
        content.write(
            "alfa",
            body=(
                "[a](/it/costi-acquisto-casa-milano#not-included)\n\n"
                "[b](/it/zones)\n\n[c](/it/)\n\n[d](/it/zones/B15?x=1#y)\n\n"
                "[e](https://example.com/qualunque)\n\n[f](#interno)\n"
            ),
        )
        assert _check(capsys, content.guides)[0] == 0

    def test_a_neighborhood_passes_only_once_it_is_in_the_sitemap(self, capsys, content: Content):
        content.write("alfa", body="[Brera](/it/milano/brera-prezzi-al-mq)\n")
        assert _check(capsys, content.guides)[0] == 1  # no blurbs: a gated hub
        content.neighborhoods({"brera": {"blurb_it": "Testo.", "blurb_en": "Copy."}})
        assert _check(capsys, content.guides)[0] == 0
        content.neighborhoods({"brera": {"blurb_it": "Testo.", "blurb_en": ""}})
        assert _check(capsys, content.guides)[0] == 1  # one language only: still gated

    def test_a_link_to_a_guide_is_always_allowed_but_a_bare_guide_path_is_not(
        self, capsys, content: Content
    ):
        content.write("alfa", body="[g](/it/guide/non-esiste-ancora)\n")
        assert _check(capsys, content.guides)[0] == 0
        content.write("alfa", body="[g](/it/guide/)\n")
        assert _check(capsys, content.guides)[0] == 1
        content.write("alfa", body="[g](/it/guide)\n")
        assert _check(capsys, content.guides)[0] == 1

    def test_an_english_page_or_a_typo_in_a_path_fails(self, capsys, content: Content):
        for href in ("/it/zone", "/it/costi-acquisto-casa-milano/", "/it/zones/ZZZ99"):
            content.write("alfa", body=f"[x]({href})\n")
            assert _check(capsys, content.guides)[0] == 1, href

    def test_output_is_only_file_code_and_line(self, capsys, content: Content):
        secret = "frase-riservata-da-non-stampare"
        content.write("alfa", body=f"{secret} [x](/it/{secret})\n")
        status, out = _check(capsys, content.guides)
        assert status == 1
        assert all(re.fullmatch(r"[a-z0-9-]+\.md: [A-Z_]+( \(line \d+\))?", line) for line in out)
        assert secret not in "\n".join(out)

    def test_a_missing_directory_is_a_usage_error(self, capsys, tmp_path: Path):
        assert check_guides.main([str(tmp_path / "nope")]) == 2
        assert "NOT_A_DIRECTORY" in capsys.readouterr().err

    def test_it_runs_as_a_standalone_script(self, content: Content):
        content.write("alfa")
        ok = subprocess.run(
            [sys.executable, str(_SCRIPT), str(content.guides), "--today", TODAY],
            capture_output=True,
            text=True,
            check=False,
        )
        assert (ok.returncode, ok.stdout.strip()) == (0, "check_guides: ok"), ok.stderr
        content.write("alfa", "NOTE\n\nqualche appunto\n\n" + guide_text())
        bad = subprocess.run(
            [sys.executable, str(_SCRIPT), str(content.guides), "--today", TODAY],
            capture_output=True,
            text=True,
            check=False,
        )
        assert bad.returncode == 1, bad.stderr
        assert bad.stdout.strip() == "alfa.md: NO_FRONT_MATTER (line 1)"
        assert "qualche appunto" not in bad.stdout + bad.stderr

    def test_it_uses_the_runtime_parser(self):
        # One implementation of the rules: the script reads files with the loader's own function.
        assert "guides.read_guide(" in _SCRIPT.read_text(encoding="utf-8")


def test_read_guide_names_the_file_in_its_errors(tmp_path: Path):
    (tmp_path / "alfa.md").write_text(guide_text(updated="x"), encoding="utf-8")
    with pytest.raises(GuideError) as exc:
        read_guide(tmp_path / "alfa.md")
    assert exc.value.file == "alfa.md"
