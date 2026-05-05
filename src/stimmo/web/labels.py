"""Translate AdjustmentBreakdown (code, params) → human-readable string.

The engine emits structured data; this web-layer module renders it for the
user's current locale (read from the active ContextVar in stimmo.i18n).
"""
from __future__ import annotations

from stimmo.i18n import gettext as _, use_locale
from stimmo.models import AdjustmentBreakdown


def render(b: AdjustmentBreakdown, locale: str | None = None) -> str:
    """Return a translated label for a breakdown entry.

    Pass `locale` explicitly when rendering outside a request context (e.g. tests).
    """
    if locale is not None:
        with use_locale(locale):
            return _render_impl(b)
    return _render_impl(b)


def _render_impl(b: AdjustmentBreakdown) -> str:
    code = b.code
    p = b.params

    match code:
        case "floor":
            lift_s = _("yes") if p.get("lift") else _("no")
            return _("Floor %(floor)s, lift: %(lift)s") % {
                "floor": p.get("floor", "?"),
                "lift": lift_s,
            }
        case "size":
            return _("Size (%(surface_m2)s m²)") % {
                "surface_m2": _fmt_g(p.get("surface_m2", "?")),
            }
        case "condition_fine":
            return _("Interior condition (%(condition)s)") % {
                "condition": p.get("condition", "?"),
            }
        case "energy_class":
            return _("Energy class %(cls)s") % {"cls": p.get("cls", "?")}
        case "outdoor":
            return _("Outdoor (%(outdoor)s)") % {"outdoor": p.get("outdoor", "?")}
        case "construction_era":
            return _("Construction era (%(era)s)") % {"era": p.get("era", "?")}
        case "orientation":
            return _("Orientation (%(orientation)s)") % {
                "orientation": p.get("orientation", "?"),
            }
        case "second_bathroom":
            return _("Second bathroom")
        case "amenities":
            return _("Amenities (OSM)")
        case "clamp":
            raw_s = f"{p.get('raw', 0):.3f}"
            clamped_s = f"{p.get('clamped', 0):.3f}"
            return _("Clamp (%(raw)s → %(clamped)s)") % {
                "raw": raw_s,
                "clamped": clamped_s,
            }
        case _:
            return code


def _fmt_g(value: object) -> str:
    try:
        f = float(value)  # type: ignore[arg-type]
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(value)
