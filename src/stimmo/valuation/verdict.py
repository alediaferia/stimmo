from __future__ import annotations

from stimmo.models import Verdict

# Tolerance band around the OMI-derived range before flagging mispricing.
LOW_TOL = 0.95
HIGH_TOL = 1.05

# Milano asking prices run ~5–10% above rogito prices. The OMI range is rogito-derived;
# we shift it up by this premium before classifying the verdict.
ASK_PREMIUM_PCT = 6.0


def classify(asking: float, low: float, high: float) -> Verdict:
    factor = 1 + ASK_PREMIUM_PCT / 100
    ask_low = low * factor
    ask_high = high * factor
    if asking < ask_low * LOW_TOL:
        return "under"
    if asking > ask_high * HIGH_TOL:
        return "over"
    return "fair"
