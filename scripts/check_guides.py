"""Check a directory of editorial guides before it is baked into a release image.

Run: `uv run python scripts/check_guides.py DIR [--today YYYY-MM-DD]`

`DIR` holds the `{slug}.md` files (`.gitkeep` is ignored). The release workflow runs this on the
guides it is about to stage, so a malformed file fails the release instead of the deploy. It
reads every file with the runtime parser (`stimmo.data.guides.read_guide`: there is one
implementation) and fails on:

* any parse error, which includes a first line that is not `---` (a file with any text above
  its front-matter) and a file name that is not `{slug}.md`;
* an `updated` date more than one day after today;
* an internal link (`/it/…`, less any `#fragment` and `?query`) that does not resolve. It must
  be `/it/`, a path in the app's sitemap, or `/it/guide/{slug}`. The last is always allowed
  because the renderer shows a link to a guide that does not exist as plain text. The sitemap
  set is built with STIMMO_CONTENT_DIR pointed at DIR's parent, where `neighborhoods.json`
  sits next to `guides/`, so a typo'd neighborhood slug, or a link to one that is not in the
  sitemap yet, is caught here.

Output is deliberately minimal: one `<file>: <ERROR_CODE> (line N)` line per problem and nothing
from the source. The release logs of this repository are public.

Exit status: 0 when everything checks out, 1 when anything is wrong, 2 for a usage error.
No network access.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from stimmo.data import guides
from stimmo.web import guide_view

# Codes this script adds to the parser's own (stimmo.data.guides.GuideErrorCode).
UPDATED_IN_FUTURE = "UPDATED_IN_FUTURE"
UNRESOLVED_LINK = "UNRESOLVED_LINK"
NOT_A_DIRECTORY = "NOT_A_DIRECTORY"

# How far ahead of today `updated` may be: a date set in a timezone ahead of the runner's.
_FUTURE_TOLERANCE = timedelta(days=1)

_FRAGMENT_OR_QUERY = re.compile(r"[?#]")


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from None


def _key_line(path: Path, key: str) -> int:
    """Line of `key:` in the front-matter of `path`, 1 if it cannot be found."""
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if line.startswith(f"{key}:"):
            return number
    return 1


def _describe(name: str, code: str, line: int = 0) -> str:
    return f"{name}: {code}" + (f" (line {line})" if line else "")


def _site_paths(directory: Path) -> frozenset[str]:
    """The paths the sitemap lists, with `directory`'s parent as the content directory.

    `neighborhoods.json` sits next to `guides/`, so which neighborhood pages are in the sitemap
    (a neighborhood is listed once both its blurbs exist) is decided by the content being
    checked, not by whatever is on this machine. Environment and caches are put back after.
    """
    names = ("STIMMO_CONTENT_DIR", "STIMMO_SHARE_DB")
    saved = {name: os.environ.get(name) for name in names}
    os.environ["STIMMO_CONTENT_DIR"] = str(directory.resolve().parent)
    # The app opens its share store when it is imported: keep that in memory, leave no file.
    os.environ.setdefault("STIMMO_SHARE_DB", ":memory:")

    from stimmo.data import neighborhoods
    from stimmo.web import app as app_module

    def clear_caches() -> None:
        guides.clear_cache()
        neighborhoods._neighborhoods_with_content.cache_clear()

    try:
        clear_caches()
        # The guides' own URLs are left out: a link to one is always allowed (below), and the
        # directory being checked may well hold a malformed file, which expanding them would raise.
        return app_module.sitemap_paths(exclude=("guide_detail",)) | {"/it/"}
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        clear_caches()


def check(directory: Path, today: date) -> list[str]:
    """Every problem in `directory`, one `<file>: <CODE> (line N)` string each; empty when the
    guides are fit to publish."""
    allowed_paths = _site_paths(directory)
    problems: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if entry.name in guides.IGNORED_FILENAMES:
            continue
        try:
            guide = guides.read_guide(entry)
        except guides.GuideError as exc:
            problems.append(_describe(entry.name, str(exc.code), exc.line))
            continue
        if guide.updated > today + _FUTURE_TOLERANCE:
            problems.append(_describe(entry.name, UPDATED_IN_FUTURE, _key_line(entry, "updated")))
        for href, line in guide_view.internal_links(guide):
            path = _FRAGMENT_OR_QUERY.split(href, maxsplit=1)[0]
            if path in allowed_paths or guide_view.GUIDE_PATH.fullmatch(path):
                continue
            problems.append(_describe(entry.name, UNRESOLVED_LINK, line))
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", type=Path, help="the directory of {slug}.md files")
    parser.add_argument(
        "--today", type=_iso_date, default=None, help="YYYY-MM-DD; replaces the clock"
    )
    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        print(_describe(str(args.directory), NOT_A_DIRECTORY), file=sys.stderr)
        return 2

    problems = check(args.directory, args.today or date.today())
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("check_guides: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
