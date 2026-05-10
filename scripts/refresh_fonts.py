"""Download, subset, and vendor Google Fonts woff2 files for stimmo.

Downloads the latin-ext block for each configured family/weight/style from the
Google Fonts CSS API, subsets to Latin + Latin-Ext unicode ranges using pyftsubset,
and writes the results to src/stimmo/web/static/fonts/.

Run: uv run python scripts/refresh_fonts.py

Requires: fonttools[woff] in dev dependencies (uv sync first).
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

GF_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

FONTS_DIR = Path(__file__).resolve().parent.parent / "src" / "stimmo" / "web" / "static" / "fonts"

# Latin + Latin-Ext unicode ranges (deduplicated, from Google Fonts definitions)
UNICODES = (
    "U+0000-00FF,U+0100-02AF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
    "U+0304,U+0308,U+0329,U+1E00-1E9F,U+1EF2-1EFF,U+2000-206F,U+2020,"
    "U+20A0-20AB,U+20AC,U+20AD-20CF,U+2113,U+2122,U+2191,U+2193,U+2212,U+2215,"
    "U+2C60-2C7F,U+A720-A7FF,U+FEFF,U+FFFD"
)

# (css_api_url, weight, style, target_filename)
FONT_SPECS: list[tuple[str, int, str, str]] = [
    (
        "https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&display=swap",
        400,
        "normal",
        "inter-tight-400.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&display=swap",
        500,
        "normal",
        "inter-tight-500.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&display=swap",
        600,
        "normal",
        "inter-tight-600.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&display=swap",
        700,
        "normal",
        "inter-tight-700.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=Instrument+Serif:ital,wght@0,400;1,400&display=swap",
        400,
        "normal",
        "instrument-serif-400.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=Instrument+Serif:ital,wght@0,400;1,400&display=swap",
        400,
        "italic",
        "instrument-serif-400-italic.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap",
        400,
        "normal",
        "jetbrains-mono-400.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap",
        500,
        "normal",
        "jetbrains-mono-500.woff2",
    ),
    (
        "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap",
        600,
        "normal",
        "jetbrains-mono-600.woff2",
    ),
]

_css_cache: dict[str, str] = {}


def _fetch_css(url: str) -> str:
    if url not in _css_cache:
        resp = httpx.get(url, headers={"User-Agent": GF_UA}, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        _css_cache[url] = resp.text
    return _css_cache[url]


def _parse_blocks(css: str) -> list[dict]:
    """Return list of {label, weight, style, url} parsed from @font-face blocks."""
    pattern = re.compile(
        r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{([^}]+)\}",
        re.DOTALL,
    )
    blocks = []
    for m in pattern.finditer(css):
        label = m.group(1).strip()
        body = m.group(2)
        weight_m = re.search(r"font-weight:\s*(\d+)", body)
        style_m = re.search(r"font-style:\s*(\w+)", body)
        url_m = re.search(r"url\((https://[^)]+\.woff2)\)", body)
        if weight_m and style_m and url_m:
            blocks.append(
                {
                    "label": label,
                    "weight": int(weight_m.group(1)),
                    "style": style_m.group(1),
                    "url": url_m.group(1),
                }
            )
    return blocks


def _subset(src: Path, dst: Path) -> None:
    pyftsubset = Path(sys.executable).parent / "pyftsubset"
    subprocess.run(
        [
            str(pyftsubset),
            str(src),
            f"--output-file={dst}",
            f"--unicodes={UNICODES}",
            "--flavor=woff2",
            "--layout-features=*",
            "--no-hinting",
            "--desubroutinize",
        ],
        check=True,
    )


def main() -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    for css_url, weight, style, target in FONT_SPECS:
        print(f"Processing {target}...")
        css = _fetch_css(css_url)
        blocks = _parse_blocks(css)

        match = None
        for preferred_label in ("latin-ext", "latin"):
            for block in blocks:
                if (
                    block["label"] == preferred_label
                    and block["weight"] == weight
                    and block["style"] == style
                ):
                    match = block
                    break
            if match:
                break

        if not match:
            print(f"  ERROR: no block found for weight={weight} style={style}", file=sys.stderr)
            sys.exit(1)

        print(f"  Downloading {match['label']} block from Google Fonts...")
        resp = httpx.get(
            match["url"], headers={"User-Agent": GF_UA}, follow_redirects=True, timeout=60
        )
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".woff2", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = Path(tmp.name)

        dst = FONTS_DIR / target
        print(f"  Subsetting → {dst.name}")
        try:
            _subset(tmp_path, dst)
        finally:
            tmp_path.unlink(missing_ok=True)

        size_kb = dst.stat().st_size / 1024
        print(f"  {size_kb:.1f} KB")

    total_kb = sum((FONTS_DIR / spec[3]).stat().st_size for spec in FONT_SPECS) / 1024
    print(f"\nAll done. {len(FONT_SPECS)} files, {total_kb:.0f} KB total in {FONTS_DIR}")


if __name__ == "__main__":
    main()
