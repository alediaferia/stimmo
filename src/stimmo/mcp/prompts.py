from __future__ import annotations

_APPRAISE_TEMPLATE = """\
Please appraise this Milan property listing: {listing}

Extract the following fields and call the `estimate_property` tool:

Required:
- address         (free-text Milan address)
- surface_m2      (positive number)
- property_type   (one of: "Abitazioni signorili", "Abitazioni civili",
                   "Abitazioni di tipo economico", "Ville e Villini")
- fine_condition  (one of: "nuovo", "ristrutturato", "abitabile", "da ristrutturare")
- floor           (integer; 0 = ground floor, -1 = basement)
- total_floors    (positive integer)
- has_lift        (true / false)
- asking_price_eur (positive number)
- construction_era (one of: "pre_war", "postwar_boom", "eighties_90s", "contemporary", "recent")

Optional — use these defaults when not clearly stated, do NOT ask the user:
- energy_class        → null
- outdoor             → "none"
- has_box             → false
- orientation         → "mixed"
- exposure            → "street"
- has_second_bathroom → false
- room_count          → null

After calling `estimate_property`, present:
1. Verdict (under-priced / fair / over-priced)
2. Estimated market range (low – mid – high EUR)
3. Key adjustment factors from the breakdown
"""


def appraise_listing(listing: str) -> str:
    return _APPRAISE_TEMPLATE.format(listing=listing)
