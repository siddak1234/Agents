"""Geo helpers — PostGIS point construction and decoding.

PostGIS stores geography as EWKB. GeoAlchemy2 hands that back as a
``WKBElement`` on load, so reading coordinates out for the API needs an
explicit decode. We do it in-process rather than with a ``ST_X``/``ST_Y``
round-trip because the list endpoint returns up to 100 properties and a
per-row SQL call would be a textbook N+1.

Coordinate order is the single easiest thing to get wrong here: PostGIS
writes ``POINT(longitude latitude)``, which is the opposite of how humans
say it. Both directions in this module are covered by unit tests that
assert against a known non-symmetric point, so a swap cannot pass CI.
"""

from __future__ import annotations

import binascii
import struct
from typing import Any, Final, NamedTuple

from geoalchemy2.elements import WKBElement, WKTElement

#: OGC WKB geometry type code for a 2D point.
_WKB_POINT = 1
#: PostGIS EWKB flag bit indicating an SRID is embedded in the header.
_EWKB_SRID_FLAG = 0x20000000
#: PostGIS EWKB flag bits for extra ordinates we do not model.
_EWKB_Z_FLAG = 0x80000000
_EWKB_M_FLAG = 0x40000000
#: Smallest possible EWKB header: one byte-order byte + a 4-byte type code.
#: Anything shorter cannot even be inspected, let alone decoded.
_EWKB_HEADER_BYTES: Final[int] = 5
#: A 2D point body is two IEEE-754 doubles.
_POINT_BODY_BYTES: Final[int] = 16
#: `POINT(x y)` needs at least two whitespace-separated ordinates.
_WKT_MIN_ORDINATES: Final[int] = 2

#: WGS 84 (EPSG:4326) coordinate domain. Named rather than inlined because
#: the failure these bounds catch is a latitude/longitude swap, and a swap is
#: only detectable at all because the two ranges differ — inlining the four
#: numbers makes the check read like arbitrary clamping instead.
_LAT_MIN: Final[float] = -90.0
_LAT_MAX: Final[float] = 90.0
_LNG_MIN: Final[float] = -180.0
_LNG_MAX: Final[float] = 180.0


class LatLng(NamedTuple):
    latitude: float
    longitude: float


def point(lat: float | None, lng: float | None) -> WKTElement | None:
    """Return a WKT POINT for a PostGIS Geography column, or None."""
    if lat is None or lng is None:
        return None
    # PostGIS convention: POINT(lng lat), NOT (lat lng). Do not swap.
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def to_lat_lng(value: Any) -> LatLng | None:
    """Decode a loaded geography column into latitude/longitude.

    Accepts what SQLAlchemy can realistically hand us for this column:
    ``None``, a ``WKBElement`` (the normal load path), a ``WKTElement``
    (an unflushed value still in the session), or a raw hex/bytes EWKB
    string. Anything it cannot parse returns ``None`` rather than raising —
    a malformed coordinate must not take down a lead listing response.
    """
    if value is None:
        return None
    if isinstance(value, WKTElement):
        return _from_wkt(str(value.data))
    raw = value.data if isinstance(value, WKBElement) else value

    if isinstance(raw, str):
        text = raw.strip()
        if text.upper().startswith(("POINT", "SRID=")):
            return _from_wkt(text)
        try:
            raw = binascii.unhexlify(text)
        except (binascii.Error, ValueError):
            return None
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if not isinstance(raw, bytes):
        return None
    return _from_ewkb(raw)


def _from_ewkb(raw: bytes) -> LatLng | None:
    if len(raw) < _EWKB_HEADER_BYTES:
        return None
    byte_order = raw[0]
    if byte_order == 0:
        endian = ">"
    elif byte_order == 1:
        endian = "<"
    else:
        return None

    (type_code,) = struct.unpack_from(f"{endian}I", raw, 1)
    offset = _EWKB_HEADER_BYTES
    if type_code & _EWKB_SRID_FLAG:
        offset += 4  # skip the embedded SRID
    if type_code & (_EWKB_Z_FLAG | _EWKB_M_FLAG):
        # 3D/measured points are not part of this schema; refusing beats
        # silently mis-reading the ordinates.
        return None
    if (type_code & 0xFF) != _WKB_POINT:
        return None
    if len(raw) < offset + _POINT_BODY_BYTES:
        return None

    lng, lat = struct.unpack_from(f"{endian}dd", raw, offset)
    return _validated(lat, lng)


def _from_wkt(text: str) -> LatLng | None:
    body = text.strip()
    if body.upper().startswith("SRID="):
        _, _, body = body.partition(";")
    body = body.strip()
    if not body.upper().startswith("POINT"):
        return None
    inner = body[body.find("(") + 1 : body.rfind(")")]
    parts = inner.split()
    if len(parts) < _WKT_MIN_ORDINATES:
        return None
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return _validated(lat, lng)


def _validated(lat: float, lng: float) -> LatLng | None:
    """Reject out-of-range coordinates — usually a lat/lng swap upstream."""
    if not (_LAT_MIN <= lat <= _LAT_MAX) or not (_LNG_MIN <= lng <= _LNG_MAX):
        return None
    return LatLng(latitude=lat, longitude=lng)
