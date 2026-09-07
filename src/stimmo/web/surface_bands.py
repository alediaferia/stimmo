"""Presentation-layer mapping of Milan room-count terms to surface ranges.

Backs the "quanto costa un bilocale / trilocale / quadrilocale" section on
`neighborhood_detail.html` (docs/street-pages-plan.md §12). This is deliberately
**not** `valuation/adjustments.py`, which is the single tuning surface for the
valuation engine and holds coefficients only — this module multiplies no
`Estimate`, produces no `AdjustmentBreakdown`, and enters no valuation pipeline.
It is a display gloss layered on top of an OMI band that has already been
computed elsewhere (`web.app._neighborhood_price_band`).

Per §12.1, the NTN surface-class buckets (`data/ntn.py`) were checked and
rejected as the rows here: their edges don't line up with what a Milanese
buyer means by *bilocale* / *trilocale* / *quadrilocale*, so pricing those
terms against the NTN buckets would overstate the headline figure. The ranges
below are stimmo's own stated presentational convention, not an official
classification — the template must say so on the page, not just here.
"""

from __future__ import annotations

from typing import NamedTuple


class RoomCountRow(NamedTuple):
    term: str  # Italian term of art, kept untranslated and italicised (§12.3)
    surface_min: float
    surface_max: float
    optional: bool  # thin-demand row (monolocale) — shown but flagged as such


# Order matches the table in docs/street-pages-plan.md §12.1.
ROOM_COUNT_SURFACE_BANDS: list[RoomCountRow] = [
    RoomCountRow(term="monolocale", surface_min=30, surface_max=45, optional=True),
    RoomCountRow(term="bilocale", surface_min=45, surface_max=60, optional=False),
    RoomCountRow(term="trilocale", surface_min=65, surface_max=90, optional=False),
    RoomCountRow(term="quadrilocale", surface_min=95, surface_max=130, optional=False),
]


def price_range_for_row(row: RoomCountRow, omi_band: tuple[float, float]) -> tuple[float, float]:
    """Price range for one room-count row given the neighborhood's OMI band.

    Widest span across the row's surface range, the same convention already
    used for multi-zone neighborhoods (lowest minimum to highest maximum):
    low surface x the band's low €/m² gives the row's low price, high surface
    x the band's high €/m² gives the row's high price.
    """
    band_lo, band_hi = omi_band
    return row.surface_min * band_lo, row.surface_max * band_hi
