"""Editorial guides: Markdown files with a small front-matter, read from the content directory.

A guide is `$STIMMO_CONTENT_DIR/guides/{slug}.md` (default directory: the repo-local,
git-ignored `var/content/`, the same one `data/neighborhoods.py` reads its blurbs from). The
prose is not committed here for the reason the neighborhood blurbs are not (stimmo is
Apache-2.0 and that text is not licensed for redistribution); it is baked into the image at
release time, and `web/app.py` serves it at `/it/guide/{slug}`. No directory, or no file, simply
means no guides: that is the state of a public clone. This module makes no network call.

File format::

    ---
    title: "Page title, shown in <title> and the search snippet"   (1-70 characters)
    h1: "Visible heading"                                            (optional; default: title)
    description: "Meta description"                                  (50-170 characters)
    published: 2026-10-09                                            (optional; default: updated)
    updated: 2026-10-09
    ai_assisted: false                                               (optional; default: true)
    ---
    <CommonMark body, no level-1 heading: the H1 comes from the front-matter>

Rules, each with a stable `GuideErrorCode`:

* Line 1 is exactly `---`. A file with any text above its front-matter is therefore never
  publishable by accident.
* One `key: value` per line up to the closing `---`. A value may be wrapped in one pair of
  double quotes, which are stripped. Nothing else from YAML is supported (no multi-line values,
  lists, anchors or comments), so the parser needs no YAML dependency.
* An unknown key is an error, which catches typos such as `updatd`.
* `ai_assisted` is `true` or `false` and nothing else (not `yes`, `no`, `True` or `1`), so a
  value meant to switch the notice off can never be silently read as "on". It decides whether the
  page shows the AI-assistance disclosure and the matching JSON-LD; a guide written by a human
  sets it to `false`.

A malformed file raises `GuideError`: it is an operator error, not "no content yet" (the same
rule as `neighborhoods.json`). `scripts/check_guides.py` runs this same parser at release time,
so a malformed file fails the release instead of the deploy.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import cache
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# The guide that the others sit under in the breadcrumb trail. Its page is optional: every use
# of this slug checks that the file exists first (see `web/app.py`'s `guide_detail`).
PILLAR_SLUG = "comprare-casa-a-milano"

TITLE_MAX = 70
DESCRIPTION_MIN = 50
DESCRIPTION_MAX = 170

# Files that may sit in the guides directory without being a guide.
IGNORED_FILENAMES: frozenset[str] = frozenset({".gitkeep"})

_REQUIRED_KEYS = ("title", "description", "updated")
_OPTIONAL_KEYS = ("h1", "published", "ai_assisted")
_KNOWN_KEYS = frozenset(_REQUIRED_KEYS) | frozenset(_OPTIONAL_KEYS)

_KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX_H1 = re.compile(r"^ {0,3}#(?:[ \t]|$)")
_ATX_ANY = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_SETEXT_H1 = re.compile(r"^ {0,3}=+[ \t]*$")

_DEFAULT_CONTENT_DIR = Path(__file__).parent.parent.parent.parent / "var" / "content"


class GuideErrorCode(StrEnum):
    """One stable code per rule. The values are what `scripts/check_guides.py` prints."""

    NO_FRONT_MATTER = "NO_FRONT_MATTER"  # line 1 is not exactly `---`
    UNTERMINATED_FRONT_MATTER = "UNTERMINATED_FRONT_MATTER"  # no closing `---`
    BAD_FRONT_MATTER_LINE = "BAD_FRONT_MATTER_LINE"  # not `key: value`
    UNBALANCED_QUOTE = "UNBALANCED_QUOTE"  # a value opens a quote and does not close it
    UNKNOWN_KEY = "UNKNOWN_KEY"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    EMPTY_VALUE = "EMPTY_VALUE"
    MISSING_TITLE = "MISSING_TITLE"
    MISSING_DESCRIPTION = "MISSING_DESCRIPTION"
    MISSING_UPDATED = "MISSING_UPDATED"
    BAD_TITLE_LENGTH = "BAD_TITLE_LENGTH"
    BAD_DESCRIPTION_LENGTH = "BAD_DESCRIPTION_LENGTH"
    BAD_DATE = "BAD_DATE"  # `updated` / `published` is not a YYYY-MM-DD date
    BAD_BOOLEAN = "BAD_BOOLEAN"  # `ai_assisted` is neither `true` nor `false`
    PUBLISHED_AFTER_UPDATED = "PUBLISHED_AFTER_UPDATED"
    EMPTY_BODY = "EMPTY_BODY"
    H1_IN_BODY = "H1_IN_BODY"
    BAD_SLUG = "BAD_SLUG"
    BAD_FILE_NAME = "BAD_FILE_NAME"  # a file in the directory that is not `{slug}.md`
    BAD_ENCODING = "BAD_ENCODING"  # not valid UTF-8


class GuideError(ValueError):
    """A guide that breaks one of the rules above.

    `code` identifies the rule and `line` the 1-based line of the file it was found on (0 when
    the rule is about the file as a whole). `file` is filled in by whoever read the file. The
    message never contains source text: release logs are public (see `scripts/check_guides.py`).
    """

    def __init__(self, code: GuideErrorCode, line: int = 0, file: str = "") -> None:
        self.code = code
        self.line = line
        self.file = file
        super().__init__(self._describe())

    def _describe(self) -> str:
        where = f" (line {self.line})" if self.line else ""
        return f"{self.file + ': ' if self.file else ''}{self.code}{where}"

    def with_file(self, file: str) -> GuideError:
        return GuideError(self.code, self.line, file)


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str
    h1: str
    description: str
    published: date
    updated: date
    body_md: str
    # 1-based line of the source file on which `body_md` starts, for diagnostics.
    body_line: int = 1
    # Whether the page carries the AI-assistance disclosure. On unless the front-matter says no.
    ai_assisted: bool = True


def _unquote(value: str, line: int) -> str:
    value = value.strip()
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise GuideError(GuideErrorCode.UNBALANCED_QUOTE, line)
        return value[1:-1]
    return value


def _parse_date(value: str, line: int) -> date:
    # Stricter than date.fromisoformat, which also takes "20261009" and "2026-W41-5".
    if not _ISO_DATE.match(value):
        raise GuideError(GuideErrorCode.BAD_DATE, line)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise GuideError(GuideErrorCode.BAD_DATE, line) from None


def _parse_bool(value: str, line: int) -> bool:
    # Exactly the two YAML spellings; YAML 1.1's yes/no/on/off are refused on purpose.
    if value not in ("true", "false"):
        raise GuideError(GuideErrorCode.BAD_BOOLEAN, line)
    return value == "true"


def _first_h1_line(lines: list[str], first_line_no: int) -> int | None:
    """Source line of the first level-1 heading outside a code fence, ATX (`# x`) or setext
    (`x` over `===`). Raw HTML `<h1>` needs no check: the renderer escapes raw HTML."""
    fence: tuple[str, int] | None = None  # (character, length) of the fence we are inside
    previous_is_text = False
    for offset, line in enumerate(lines):
        match = _FENCE.match(line)
        if fence is not None:
            # A closing fence: same character, at least as long, nothing after it.
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1]
                and not line[match.end() :].strip()
            ):
                fence = None
            previous_is_text = False
            continue
        if match:
            fence = (match.group(1)[0], len(match.group(1)))
            previous_is_text = False
            continue
        if _ATX_H1.match(line):
            return first_line_no + offset
        if previous_is_text and _SETEXT_H1.match(line):
            return first_line_no + offset - 1  # the heading is the line above its underline
        previous_is_text = bool(line.strip()) and not _ATX_ANY.match(line)
    return None


def parse_guide(text: str, slug: str) -> Guide:
    """Parse the text of `{slug}.md`. Raises `GuideError` on the first broken rule."""
    if not SLUG_RE.match(slug):
        raise GuideError(GuideErrorCode.BAD_SLUG)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[0] != "---":
        raise GuideError(GuideErrorCode.NO_FRONT_MATTER, 1)

    fields: dict[str, tuple[str, int]] = {}
    closing: int | None = None
    for index in range(1, len(lines)):
        line_no = index + 1
        raw = lines[index]
        if raw == "---":
            closing = index
            break
        if not raw.strip():
            continue
        match = _KEY_LINE.match(raw)
        if not match:
            raise GuideError(GuideErrorCode.BAD_FRONT_MATTER_LINE, line_no)
        key, value = match.group(1), _unquote(match.group(2), line_no)
        if key not in _KNOWN_KEYS:
            raise GuideError(GuideErrorCode.UNKNOWN_KEY, line_no)
        if key in fields:
            raise GuideError(GuideErrorCode.DUPLICATE_KEY, line_no)
        if not value:
            raise GuideError(GuideErrorCode.EMPTY_VALUE, line_no)
        fields[key] = (value, line_no)
    if closing is None:
        raise GuideError(GuideErrorCode.UNTERMINATED_FRONT_MATTER, 1)

    for key, code in (
        ("title", GuideErrorCode.MISSING_TITLE),
        ("description", GuideErrorCode.MISSING_DESCRIPTION),
        ("updated", GuideErrorCode.MISSING_UPDATED),
    ):
        if key not in fields:
            raise GuideError(code, 1)

    title, title_line = fields["title"]
    if not 1 <= len(title) <= TITLE_MAX:
        raise GuideError(GuideErrorCode.BAD_TITLE_LENGTH, title_line)
    description, description_line = fields["description"]
    if not DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX:
        raise GuideError(GuideErrorCode.BAD_DESCRIPTION_LENGTH, description_line)
    updated = _parse_date(*fields["updated"])
    published = _parse_date(*fields["published"]) if "published" in fields else updated
    if published > updated:
        raise GuideError(GuideErrorCode.PUBLISHED_AFTER_UPDATED, fields["published"][1])
    h1 = fields["h1"][0] if "h1" in fields else title
    ai_assisted = _parse_bool(*fields["ai_assisted"]) if "ai_assisted" in fields else True

    body_lines = lines[closing + 1 :]
    first = next((i for i, line in enumerate(body_lines) if line.strip()), None)
    if first is None:
        raise GuideError(GuideErrorCode.EMPTY_BODY, closing + 1)
    body_line = closing + 2 + first
    body_lines = body_lines[first:]
    h1_line = _first_h1_line(body_lines, body_line)
    if h1_line is not None:
        raise GuideError(GuideErrorCode.H1_IN_BODY, h1_line)

    return Guide(
        slug=slug,
        title=title,
        h1=h1,
        description=description,
        published=published,
        updated=updated,
        body_md="\n".join(body_lines).rstrip(),
        body_line=body_line,
        ai_assisted=ai_assisted,
    )


def slug_from_filename(name: str) -> str:
    """The slug of a guide file name, or `GuideError(BAD_FILE_NAME)` unless it is `{slug}.md`."""
    stem, dot, suffix = name.rpartition(".")
    if not dot or suffix != "md" or not SLUG_RE.match(stem):
        raise GuideError(GuideErrorCode.BAD_FILE_NAME, 0, name)
    return stem


def read_guide(path: Path) -> Guide:
    """Read and parse one guide file. Every `GuideError` raised carries the file name."""
    name = path.name
    try:
        slug = slug_from_filename(name)
        if not path.is_file():
            raise GuideError(GuideErrorCode.BAD_FILE_NAME)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise GuideError(GuideErrorCode.BAD_ENCODING) from None
        return parse_guide(text, slug)
    except GuideError as exc:
        raise exc.with_file(name) from None


def _guides_dir() -> Path:
    content_dir = os.environ.get("STIMMO_CONTENT_DIR", str(_DEFAULT_CONTENT_DIR))
    return Path(content_dir) / "guides"


@cache
def _load_guides() -> dict[str, Guide]:
    """Every guide in the content directory, by slug. Cached per process: a restart picks up
    edits, the same as the neighborhood blurbs. `clear_cache()` forces a reload."""
    directory = _guides_dir()
    if not directory.is_dir():
        return {}
    loaded: dict[str, Guide] = {}
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if entry.name in IGNORED_FILENAMES:
            continue
        guide = read_guide(entry)
        loaded[guide.slug] = guide
    return loaded


def clear_cache() -> None:
    _load_guides.cache_clear()


def list_guides() -> tuple[Guide, ...]:
    """All guides, sorted by slug. Empty when there is no guides directory."""
    return tuple(sorted(_load_guides().values(), key=lambda g: g.slug))


def guide_for_slug(slug: str) -> Guide | None:
    """The guide with this slug, or None. Looks the slug up among the loaded guides and never
    builds a path from it, so any string is safe to pass."""
    return _load_guides().get(slug)
