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

Stored-blob format (share_store.py):
    zlib.compress(compact_json_bytes)  — same bytes WITHOUT the version prefix.
    The store path adds an "m" key with amenity marker positions+kinds (no names).
"""

from __future__ import annotations

import base64
import json
import zlib
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import ValidationError

from stimmo.models import AmenityItem, AmenityScore, Property

if TYPE_CHECKING:
    pass

_VERSION = b"\x01"
_MAX_DECOMPRESSED = 64_000  # bytes — sanity guard against zip-bombs

# Fixed kind order for the "m" marker list in the stored blob — a FROZEN wire
# format: the index into this tuple is what gets stored, so entries must never be
# reordered or removed (only appended) without breaking existing stored blobs.
_MarkerKind = Literal["metro", "tram", "park", "supermarket", "school", "pharmacy"]
_MARKER_KINDS: tuple[_MarkerKind, ...] = get_args(_MarkerKind)
_MARKER_KIND_TO_IDX: dict[str, int] = {k: i for i, k in enumerate(_MARKER_KINDS)}
_MARKER_CAP = 12  # matches _extract_items cap in data/amenities.py


class ShareTokenError(ValueError):
    """Raised when a token cannot be decoded (malformed, truncated, version mismatch)."""


# ---------------------------------------------------------------------------
# Payload / blob primitives (internal helpers shared by legacy and store paths)
# ---------------------------------------------------------------------------


def _payload_dict(
    prop: Property,
    lat: float,
    lon: float,
    amenity: AmenityScore,
) -> dict[str, Any]:
    """Build the JSON-serialisable payload dict (legacy shape, no markers)."""
    return {
        "v": 1,
        "p": prop.model_dump(mode="json"),
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "a": amenity.model_dump(mode="json", exclude={"items_within_500m"}),
    }


def _compress_payload(payload: dict[str, Any]) -> bytes:
    """Serialise a payload dict to zlib-compressed compact JSON bytes."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return zlib.compress(raw, level=9)


def _decompress_payload(blob: bytes) -> dict[str, Any]:
    """Decompress and parse a blob; raises ShareTokenError on any failure."""
    try:
        raw = zlib.decompress(blob)
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

    return payload


# ---------------------------------------------------------------------------
# Legacy public API — unchanged observable behaviour (D5)
# ---------------------------------------------------------------------------


def encode(prop: Property, lat: float, lon: float, amenity: AmenityScore) -> str:
    """Encode a Property + geocode + amenity snapshot into a compact URL-safe token.

    Legacy format: b'\\x01' + blob → base64url (no padding).
    Markers (items_within_500m) are intentionally excluded to keep the token
    small; per decision Q5 they are only added in the stored-blob path.
    """
    payload = _payload_dict(prop, lat, lon, amenity)
    blob = _compress_payload(payload)
    token_bytes = _VERSION + blob
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

    blob = token_bytes[1:]
    payload = _decompress_payload(blob)
    return _payload_to_tuple(payload)


# ---------------------------------------------------------------------------
# Stored-blob path — includes amenity markers (Step 1+)
# ---------------------------------------------------------------------------


def encode_blob(prop: Property, lat: float, lon: float, amenity: AmenityScore) -> bytes:
    """Produce the stored blob: same compressed payload as legacy but WITH marker list.

    The blob is zlib-compressed compact JSON, WITHOUT the version prefix byte
    (the store is codec-agnostic; the version prefix is only used in URL tokens).
    The "m" key carries [[kind_idx, lat6, lon6], ...] capped at _MARKER_CAP=12,
    in kind-index order, names omitted (D6).
    """
    payload = _payload_dict(prop, lat, lon, amenity)

    # Build marker list: kind index + rounded coords, names omitted, capped at 12.
    items = amenity.items_within_500m or []
    markers: list[list[int | float]] = []
    for item in items[:_MARKER_CAP]:
        idx = _MARKER_KIND_TO_IDX.get(item.kind)
        if idx is None:
            continue  # unknown kind — skip defensively
        markers.append([idx, round(item.lat, 6), round(item.lon, 6)])
    payload["m"] = markers

    return _compress_payload(payload)


def decode_blob(blob: bytes) -> tuple[Property, float, float, AmenityScore]:
    """Decode a stored blob back to (Property, lat, lon, AmenityScore).

    The returned AmenityScore has items_within_500m populated from the "m" marker
    list (kind+coords only, names=None), which is enough to render map markers.
    Raises ShareTokenError on any malformed input.
    """
    payload = _decompress_payload(blob)

    prop, lat, lon, amen_base = _payload_to_tuple(payload)

    # Reconstruct items_within_500m from the "m" marker list if present.
    raw_markers = payload.get("m")
    if raw_markers and isinstance(raw_markers, list):
        items: list[AmenityItem] = []
        for entry in raw_markers[:_MARKER_CAP]:
            try:
                kind_idx, lat_m, lon_m = entry
                kind = _MARKER_KINDS[int(kind_idx)]
                items.append(AmenityItem(kind=kind, name=None, lat=float(lat_m), lon=float(lon_m)))
            except (TypeError, ValueError, IndexError, KeyError):
                continue  # skip malformed entries defensively

        # Rebuild AmenityScore with items populated (all other counts already decoded).
        amen = AmenityScore(**{**amen_base.model_dump(exclude={"items_within_500m"}),
                               "items_within_500m": [i.model_dump() for i in items]})
        return prop, lat, lon, amen

    return prop, lat, lon, amen_base


# ---------------------------------------------------------------------------
# Dual-path resolve helper (Step 3)
# ---------------------------------------------------------------------------


def resolve(
    identifier: str,
    store: Any,  # ShareStore protocol; typed loosely to avoid circular import
) -> tuple[Property, float, float, AmenityScore]:
    """Dual-path resolution (D5).

    Short identifiers (<=10 chars, base62 [0-9A-Za-z]) are tried against the
    store first.  On a hit the blob is decoded via decode_blob.  On a miss (or
    if the identifier is clearly a legacy token — long or contains base64url
    chars), fall back to the legacy decode().

    Raises ShareTokenError if neither path resolves.
    """
    _is_short = len(identifier) <= 10 and identifier.isalnum()

    if _is_short:
        blob = store.get(identifier)
        if blob is not None:
            return decode_blob(blob)
        # Store miss — fall through to legacy decode (covers edge cases where a
        # short but legacy token exists, extremely unlikely but handled per D5).

    # Legacy fallback: raises ShareTokenError if the token is invalid.
    return decode(identifier)


# ---------------------------------------------------------------------------
# Shared internal helper
# ---------------------------------------------------------------------------


def _payload_to_tuple(
    payload: dict[str, Any],
) -> tuple[Property, float, float, AmenityScore]:
    """Extract (Property, lat, lon, AmenityScore) from a decoded payload dict.

    Used by both decode() and decode_blob() after payload validation.
    """
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
        # items_within_500m is excluded from the "a" key encoding; populated
        # separately from "m" in decode_blob.
        amen_data = payload.get("a", {})
        amen = AmenityScore.model_validate(amen_data)
    except (ValidationError, TypeError) as exc:
        raise ShareTokenError(f"AmenityScore decode error: {exc}") from exc

    return prop, lat, lon, amen
