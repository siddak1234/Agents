"""Address normalization + deterministic hashing.

The address hash is our primary dedup key across every ingestion
source. It must be **deterministic**, **case-insensitive**,
**punctuation-insensitive**, and stable across minor formatting
differences (``St`` vs ``Street``, ``#4`` vs ``Unit 4``).

Strategy:
    1. Parse via ``usaddress`` (USPS-adjacent grammar; not USPS-perfect
       but the pragmatic industry default).
    2. Rebuild a canonical tuple ``(street_number, street_name, unit,
       city, state, postal_code)``.
    3. Apply USPS suffix normalization (``STREET -> ST``, ``AVENUE ->
       AVE``, etc.) using a fixed alias table.
    4. Uppercase, strip punctuation, single-space.
    5. sha256 the "|"-joined canonical form.

Callers who need a stronger match (against reference data with a
different formatter) can additionally use ``rapidfuzz`` for a fuzzy
score fallback — but this hash is the primary key.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final

import usaddress

# USPS Publication 28 suffix abbreviations — abbreviated form is canonical.
# This is a subset covering >99% of real US addresses.
USPS_SUFFIX_ALIASES: Final[dict[str, str]] = {
    "AVENUE": "AVE",
    "AVE.": "AVE",
    "BOULEVARD": "BLVD",
    "BLVD.": "BLVD",
    "COURT": "CT",
    "CT.": "CT",
    "DRIVE": "DR",
    "DR.": "DR",
    "LANE": "LN",
    "LN.": "LN",
    "PARKWAY": "PKWY",
    "PKWY.": "PKWY",
    "PLACE": "PL",
    "PL.": "PL",
    "ROAD": "RD",
    "RD.": "RD",
    "STREET": "ST",
    "ST.": "ST",
    "TERRACE": "TER",
    "TERR": "TER",
    "TRAIL": "TRL",
    "WAY": "WAY",
    "CIRCLE": "CIR",
    "HIGHWAY": "HWY",
    "HWY.": "HWY",
}

USPS_DIRECTIONAL_ALIASES: Final[dict[str, str]] = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
}

USPS_UNIT_ALIASES: Final[dict[str, str]] = {
    "APARTMENT": "APT",
    "APT.": "APT",
    "SUITE": "STE",
    "STE.": "STE",
    "UNIT": "UNIT",
    "#": "UNIT",
}

_ALPHANUM = re.compile(r"[^A-Z0-9 ]+")
_MULTISPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalizedAddress:
    street_number: str
    street_name: str
    unit: str
    city: str
    state: str
    postal_code: str

    @property
    def canonical_form(self) -> str:
        return "|".join(
            (
                self.street_number,
                self.street_name,
                self.unit,
                self.city,
                self.state,
                self.postal_code,
            )
        )


def _clean_token(value: str) -> str:
    upper = value.upper()
    stripped = _ALPHANUM.sub(" ", upper)
    return _MULTISPACE.sub(" ", stripped).strip()


def _normalize_street_name_parts(parts: list[str]) -> str:
    out: list[str] = []
    for p in parts:
        token = _clean_token(p)
        if token in USPS_SUFFIX_ALIASES:
            out.append(USPS_SUFFIX_ALIASES[token])
        elif token in USPS_DIRECTIONAL_ALIASES:
            out.append(USPS_DIRECTIONAL_ALIASES[token])
        else:
            out.append(token)
    return " ".join(out).strip()


def normalize_address(raw: str) -> NormalizedAddress:
    """Parse and normalize a free-form US address string.

    Never raises for malformed input — falls back to a whole-string dump
    into ``street_name`` so downstream dedup can at least attempt an
    exact-string match. Callers who need strictness should check the
    returned fields for emptiness.
    """
    try:
        tagged, _ = usaddress.tag(raw or "")
    except usaddress.RepeatedLabelError:
        # Ambiguous parse — surface a degraded but still deterministic form.
        return NormalizedAddress(
            street_number="",
            street_name=_clean_token(raw),
            unit="",
            city="",
            state="",
            postal_code="",
        )

    street_number = _clean_token(tagged.get("AddressNumber", ""))
    # Order is the USPS component order, not `tagged`'s — iterating the key
    # tuple is what reassembles "N MAIN ST SW" rather than whatever sequence
    # the parser happened to emit.
    street_parts: list[str] = [
        tagged[key]
        for key in (
            "StreetNamePreDirectional",
            "StreetNamePreType",
            "StreetName",
            "StreetNamePostType",
            "StreetNamePostDirectional",
        )
        if key in tagged
    ]
    street_name = _normalize_street_name_parts(street_parts)

    unit_type = _clean_token(tagged.get("OccupancyType", ""))
    unit_identifier = _clean_token(tagged.get("OccupancyIdentifier", ""))
    if unit_type in USPS_UNIT_ALIASES:
        unit_type = USPS_UNIT_ALIASES[unit_type]
    unit = f"{unit_type} {unit_identifier}".strip() if unit_identifier else ""

    city = _clean_token(tagged.get("PlaceName", ""))
    state = _clean_token(tagged.get("StateName", ""))[:2]
    postal = _clean_token(tagged.get("ZipCode", ""))[:5]

    return NormalizedAddress(
        street_number=street_number,
        street_name=street_name,
        unit=unit,
        city=city,
        state=state,
        postal_code=postal,
    )


def hash_address(raw: str) -> str:
    """SHA-256 hex digest of the normalized canonical form of ``raw``."""
    normalized = normalize_address(raw)
    return hashlib.sha256(normalized.canonical_form.encode("utf-8")).hexdigest()


def compose_display_address(n: NormalizedAddress) -> str:
    """Rebuild a human-facing address string from a normalized record."""
    line1_parts = [p for p in (n.street_number, n.street_name, n.unit) if p]
    line1 = " ".join(line1_parts)
    tail_parts = [p for p in (n.city, n.state, n.postal_code) if p]
    tail = ", ".join(tail_parts) if len(tail_parts) > 1 else " ".join(tail_parts)
    return f"{line1}, {tail}" if line1 and tail else line1 or tail
