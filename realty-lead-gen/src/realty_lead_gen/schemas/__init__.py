"""Pydantic v2 DTOs.

We do not expose SQLAlchemy models directly. All API + inter-layer
data crossings use these DTOs so:
    * We can evolve the DB independently of the wire.
    * The API contract is machine-checkable via OpenAPI.
    * Adapters produce the same normalized shape regardless of source.
"""

from realty_lead_gen.schemas.listing import ListingIngestDTO, PhotoDTO, RawListing
from realty_lead_gen.schemas.property import PropertyDTO

__all__ = ["ListingIngestDTO", "PhotoDTO", "PropertyDTO", "RawListing"]
