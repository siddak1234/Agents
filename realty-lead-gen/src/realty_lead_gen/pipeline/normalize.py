"""Normalize a RawListing into upsert-ready ORM data.

Address hashing happens here — the pipeline never calls
``hash_address`` outside of this module so we have one place to
version the hashing scheme.
"""

from __future__ import annotations

from realty_lead_gen.schemas.listing import ListingIngestDTO, RawListing
from realty_lead_gen.utils.addr import hash_address


def normalize_raw_listing(raw: RawListing) -> ListingIngestDTO:
    return ListingIngestDTO(
        address_hash=hash_address(raw.display_address),
        raw=raw,
    )
