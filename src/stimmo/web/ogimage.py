"""Pillow-based OG card renderer for stimmo share links.

Produces a 1200 × 630 PNG that mirrors the on-page verdict card:
  - verdict eyebrow + colour-coded verdict sentence
  - address
  - the OMI/ask gauge with the asking-price marker
  - the four verdict stats (Δ vs typical ask, adjusted €/m², asking €/m², multiplier)
  - stimmo wordmark + URL bug

The render is pure-Python — no headless browser required.  Font: the full-charset
Inter Tight variable TTF bundled at static/fonts/inter-tight-var.ttf (OFL).
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from stimmo.models import Estimate, Property, Verdict
from stimmo.valuation.verdict import HIGH_TOL

# ---------------------------------------------------------------------------
# Palette — mirrors CSS custom properties in base.html
# ---------------------------------------------------------------------------

_PAPER = (251, 248, 242)  # oklch(0.975 0.008 82) ≈
_PAPER_2 = (244, 240, 232)  # oklch(0.955 0.010 82) ≈ — OMI band fill
_PAPER_RULE = (224, 218, 206)  # oklch(0.88 0.012 82) ≈
_INK = (38, 36, 60)  # oklch(0.22 0.012 250) ≈
_INK_MUTED = (118, 118, 140)  # oklch(0.52 0.010 250) ≈
_INK_FADE = (165, 165, 180)  # oklch(0.68 0.008 250) ≈

_ASK_FILL = (245, 231, 214)  # oklch(0.94 0.04 38) ≈ — typical-ask band fill
_ASK_BORDER = (208, 174, 139)  # oklch(0.78 0.08 38) ≈

_GOOD = (61, 145, 87)  # oklch(0.55 0.10 145) ≈
_WARN = (172, 143, 48)  # oklch(0.65 0.12 75) ≈
_BAD = (180, 67, 45)  # oklch(0.55 0.13 28) ≈

_VERDICT_COLOR: dict[str, tuple[int, int, int]] = {
    "under": _GOOD,
    "fair": _WARN,
    "over": _BAD,
}

# (prefix, emphasis, suffix) — mirrors the verdict_copy hero in result.html
_VERDICT_SENTENCE: dict[str, tuple[str, str, str]] = {
    "under": ("A rare ", "good deal", "."),
    "fair": ("The asking price reads ", "fair", "."),
    "over": ("This listing is ", "over-priced", "."),
}

_VERDICT_EYEBROW: dict[str, str] = {
    "under": "VERDICT · UNDER-PRICED",
    "fair": "VERDICT · FAIR",
    "over": "VERDICT · OVER-PRICED",
}

# ---------------------------------------------------------------------------
# Card dimensions
# ---------------------------------------------------------------------------

_W, _H = 1200, 630
_PAD = 64
_X0 = _PAD + 12  # content left edge
_ACCENT_BAR = 6  # left-side accent stripe width

# ---------------------------------------------------------------------------
# Font
# ---------------------------------------------------------------------------

_FONTS_DIR = Path(__file__).parent / "static" / "fonts"
# Full-charset Inter Tight variable TTF (wght 100–900), bundled from Google Fonts
# (OFL).  The site's woff2 webfonts are per-page subsets and cannot render
# arbitrary addresses/prices, so the OG card carries its own complete font.
_FONT_PATH = _FONTS_DIR / "inter-tight-var.ttf"


def _font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load Inter Tight at the given size, setting the weight variation axis."""
    if not _FONT_PATH.exists():
        # Graceful fallback to Pillow built-in bitmap font (no text metrics, small)
        return ImageFont.load_default()
    font = ImageFont.truetype(str(_FONT_PATH), size)
    # non-variable build or unsupported Pillow — default instance is fine
    with contextlib.suppress(OSError, AttributeError):
        font.set_variation_by_axes([weight])
    return font


# ---------------------------------------------------------------------------
# Formatters (no locale dependency — OG cards are always en-style numerals)
# ---------------------------------------------------------------------------


def _fmt_eur(n: float) -> str:
    return f"€ {round(n):,}".replace(",", " ")  # narrow no-break space


def _fmt_semester(semester: str) -> str:
    """'2024-2' → '2024 H2'; pass through anything unexpected."""
    year, _, half = semester.partition("-")
    return f"{year} H{half}" if half else semester


def _clip(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> str:
    """Truncate ``text`` with an ellipsis so it fits within ``max_w`` pixels."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render(est: Estimate, prop: Property) -> bytes:
    """Render a 1200×630 OG card mirroring the verdict card; return PNG bytes."""
    verdict: Verdict = est.verdict
    vcolor = _VERDICT_COLOR.get(verdict, _INK)
    asking = prop.asking_price_eur

    img = Image.new("RGB", (_W, _H), _PAPER)
    draw = ImageDraw.Draw(img)

    # ── Left accent stripe (verdict colour) ──
    draw.rectangle([0, 0, _ACCENT_BAR, _H], fill=vcolor)

    # ── Verdict eyebrow ──
    draw.text((_X0, 50), _VERDICT_EYEBROW[verdict], font=_font(17, 600), fill=_INK_MUTED)

    # ── Verdict sentence (emphasis word in the verdict colour) ──
    prefix, emph, suffix = _VERDICT_SENTENCE[verdict]
    f_h = _font(46, 500)
    f_he = _font(46, 700)
    hx, hy = _X0, 80
    draw.text((hx, hy), prefix, font=f_h, fill=_INK)
    hx += draw.textlength(prefix, font=f_h)
    draw.text((hx, hy), emph, font=f_he, fill=vcolor)
    hx += draw.textlength(emph, font=f_he)
    draw.text((hx, hy), suffix, font=f_h, fill=_INK)

    # ── Address · size ──
    f_addr = _font(30, 500)
    size_txt = f"  ·  {prop.surface_m2:g} m²"
    size_w = draw.textlength(size_txt, font=f_addr)
    addr = _clip(draw, prop.address.split(",")[0].strip(), f_addr, _W - _PAD - _X0 - size_w)
    draw.text((_X0, 148), addr, font=f_addr, fill=_INK_MUTED)
    draw.text((_X0 + draw.textlength(addr, font=f_addr), 148), size_txt, font=f_addr, fill=_INK)

    # ── Gauge (OMI band + ask band + asking marker) ──
    gx0, gx1 = _X0, _W - _PAD
    g_lo = min(est.ask_range_low_eur, est.range_low_eur) * 0.92
    verdict_high = est.ask_range_high_eur * HIGH_TOL
    g_hi = max(verdict_high, asking, est.range_high_eur) * 1.06

    def gx(v: float) -> float:
        return gx0 + (v - g_lo) / (g_hi - g_lo) * (gx1 - gx0)

    gy = 272
    draw.line([(gx0, gy), (gx1, gy)], fill=_PAPER_RULE, width=2)
    draw.rounded_rectangle(
        [gx(est.range_low_eur), gy - 9, gx(est.range_high_eur), gy + 9],
        radius=2,
        fill=_PAPER_2,
        outline=_PAPER_RULE,
        width=1,
    )
    draw.rounded_rectangle(
        [gx(est.ask_range_low_eur), gy - 13, gx(verdict_high), gy + 13],
        radius=2,
        fill=_ASK_FILL,
        outline=_ASK_BORDER,
        width=1,
    )

    # Asking marker + flag
    ax = gx(asking)
    f_flag = _font(21, 600)
    flag = f"Asking · {_fmt_eur(asking)}"
    fb = draw.textbbox((0, 0), flag, font=f_flag)
    fw, fh = fb[2] - fb[0], fb[3] - fb[1]
    fp = 9
    box_w = fw + 2 * fp
    fx0 = min(max(ax - box_w / 2, gx0), gx1 - box_w)
    fy1 = gy - 24
    fy0 = fy1 - fh - 2 * fp
    draw.line([(ax, gy - 22), (ax, gy + 20)], fill=vcolor, width=3)
    draw.rounded_rectangle([fx0, fy0, fx0 + box_w, fy1], radius=5, fill=vcolor)
    draw.text((fx0 + fp, fy0 + fp - fb[1]), flag, font=f_flag, fill=_PAPER)

    # Gauge legend
    leg_y = gy + 28
    f_leg = _font(16, 400)

    def _legend(x: float, fill, border, text: str) -> float:
        draw.rounded_rectangle(
            [x, leg_y + 2, x + 16, leg_y + 16], radius=2, fill=fill, outline=border, width=1
        )
        draw.text((x + 23, leg_y), text, font=f_leg, fill=_INK_MUTED)
        return x + 23 + draw.textlength(text, font=f_leg) + 30

    lx = _legend(_X0, _PAPER_2, _PAPER_RULE, "OMI rogito band")
    _legend(lx, _ASK_FILL, _ASK_BORDER, f"Typical ask band (+{round(est.ask_premium_pct)} %)")

    # ── Verdict stats (mirrors the four .vstat cells) ──
    delta = est.asking_vs_ask_mid_pct
    delta_color = _BAD if delta > 0 else _GOOD if delta < 0 else _INK
    stats = [
        ("Δ VS TYPICAL ASK MID", f"{delta:+.1f} %", delta_color),
        ("ADJUSTED €/M²", _fmt_eur(est.adjusted_eur_m2_mid), _INK),
        ("ASKING €/M²", _fmt_eur(asking / prop.surface_m2), _INK),
        ("MULTIPLIER ON OMI BAND", f"×{est.multiplier:.3f}", _INK),
    ]
    f_lbl = _font(16, 500)
    f_val = _font(42, 700)
    col_w = (_W - _PAD - _X0) // len(stats)
    for i, (lbl, val, color) in enumerate(stats):
        cx = _X0 + i * col_w
        draw.text((cx, 356), lbl, font=f_lbl, fill=_INK_MUTED)
        draw.text((cx, 384), val, font=f_val, fill=color)

    # ── Footer: wordmark + tagline + OMI tag ──
    foot_y = _H - _PAD - 12
    draw.text((_X0, foot_y), "stimmo", font=_font(26, 700), fill=_INK)
    draw.text(
        (_X0, foot_y + 34),
        "stimmo.it · Milan fair-price check",
        font=_font(18, 400),
        fill=_INK_MUTED,
    )
    f_tag = _font(16, 400)
    tag = f"OMI {_fmt_semester(est.omi_quote.semester)} · no ML"
    tb = draw.textbbox((0, 0), tag, font=f_tag)
    draw.text((_W - _PAD - (tb[2] - tb[0]), foot_y + 40), tag, font=f_tag, fill=_INK_FADE)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
