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

Optional — ask the user before calling the tool if not clearly stated in the listing:
- energy_class        (one of: "A4","A3","A2","A1","B","C","D","E","F","G"; null if truly unknown)
- outdoor             (one of: "none","balcony","terrace","garden")
- room_count          (positive integer, Italian "locali")
- has_second_bathroom (true / false)

Optional — use these defaults silently, do NOT ask the user:
- has_box             → false
- orientation         → "mixed"
- exposure            → "street"

After calling `estimate_property`, present:
1. Verdict (under-priced / fair / over-priced)
2. Estimated market range (low – mid – high EUR)
3. Key adjustment factors from the breakdown
"""


def appraise_listing(listing: str) -> str:
    return _APPRAISE_TEMPLATE.format(listing=listing)
