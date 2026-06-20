"""Share-token encoding and decoding for stimmo estimate links.

Token format (version 1):
    b'\x01' + zlib.compress(compact_json_bytes)  →  base64url (no padding)

The JSON payload carries the minimal inputs needed to skip the slow live services
(Nominatim geocode + Overpass amenities) and reproduce the full estimate cheaply:
  - all Property fields verbatim
  - geocode result: lat, lon
  - AmenityScore dump (Overpass result)

On decode the caller recomputes zone lookup, OMI lookup, engine.estimate, history,
and NTN from the bundled data — fast, deterministic, and stateless.  If the
bundled OMI data changes (rare semester bumps) the displayed number can drift
slightly; that satisfies the speed requirement and keeps tokens small.

Typical encoded length: ~420–480 base64url chars for a normal property.
Version byte prefix gives forward-compat headroom for future payload changes.
"""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

from pydantic import ValidationError

from stimmo.models import AmenityScore, Property

_VERSION = b"\x01"
_MAX_DECOMPRESSED = 64_000  # bytes — sanity guard against zip-bombs


class ShareTokenError(ValueError):
    """Raised when a token cannot be decoded (malformed, truncated, version mismatch)."""


def encode(prop: Property, lat: float, lon: float, amenity: AmenityScore) -> str:
    """Encode a Property + geocode + amenity snapshot into a compact URL-safe token."""
    payload: dict[str, Any] = {
        "v": 1,
        "p": prop.model_dump(mode="json"),
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "a": amenity.model_dump(mode="json", exclude={"items_within_500m"}),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    compressed = zlib.compress(raw, level=9)
    token_bytes = _VERSION + compressed
    return base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")


def decode(token: str) -> tuple[Property, float, float, AmenityScore]:
    """Decode a share token back to (Property, lat, lon, AmenityScore).

    Raises ShareTokenError on any malformed or incompatible input.
    """
    # Restore base64 padding
    padded = token + "=" * (-len(token) % 4)
    try:
        token_bytes = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ShareTokenError("Invalid base64 encoding") from exc

    if len(token_bytes) < 2:
        raise ShareTokenError("Token too short")

    version = token_bytes[:1]
    if version != _VERSION:
        raise ShareTokenError(f"Unsupported token version: {version!r}")

    compressed = token_bytes[1:]
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        raise ShareTokenError("Decompression failed") from exc

    if len(raw) > _MAX_DECOMPRESSED:
        raise ShareTokenError("Decompressed payload too large")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ShareTokenError("JSON parse error") from exc

    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ShareTokenError("Payload version mismatch or wrong structure")

    try:
        prop = Property.model_validate(payload["p"])
    except (KeyError, ValidationError) as exc:
        raise ShareTokenError(f"Property decode error: {exc}") from exc

    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ShareTokenError("Missing or invalid lat/lon") from exc

    try:
        # items_within_500m was excluded from encoding (too verbose for a URL);
        # the share view renders without the map markers, which is acceptable.
        amen_data = payload.get("a", {})
        amen = AmenityScore.model_validate(amen_data)
    except (ValidationError, TypeError) as exc:
        raise ShareTokenError(f"AmenityScore decode error: {exc}") from exc

    return prop, lat, lon, amen
