"""Ingestion / listing DTOs.

`RawListing` is what every source adapter emits. The pipeline
normalizes it into a canonical `ListingIngestDTO` + upserts into
`Property`, `Listing`, `Photo`, etc.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from realty_lead_gen.models.listing import ListingIntent, ListingStatus
from realty_lead_gen.models.property import PropertyType, SnapshotSource


class PhotoDTO(BaseModel):
    """One photo attached to a listing."""

    model_config = ConfigDict(frozen=True)

    url: HttpUrl
    caption: str | None = None
    order_index: int = 0


class RawListing(BaseModel):
    """Source-agnostic listing shape emitted by an adapter.

    Adapters map their vendor-specific payload into this shape. Missing
    fields are ``None``; the pipeline is tolerant of partial records
    (many public-record sources return only address + owner).
    """

    model_config = ConfigDict(frozen=True)

    source: SnapshotSource
    source_listing_id: str = Field(min_length=1, max_length=128)
    source_record_id: str | None = None

    # Address
    display_address: str = Field(min_length=1, max_length=512)
    street_number: str | None = None
    street_name: str | None = None
    unit: str | None = None
    city: str = Field(min_length=1, max_length=96)
    state: str = Field(min_length=2, max_length=2)
    postal_code: str = Field(min_length=5, max_length=10)
    latitude: float | None = None
    longitude: float | None = None
    county_fips: str | None = None
    apn: str | None = None

    # Physical
    property_type: PropertyType = PropertyType.single_family
    year_built: int | None = None
    lot_size_sqft: int | None = None
    living_area_sqft: int | None = None
    bedrooms: int | None = None
    bathrooms: Decimal | None = None

    # Listing
    intent: ListingIntent = ListingIntent.for_sale
    status: ListingStatus = ListingStatus.unknown
    list_price_cents: int | None = None
    original_list_price_cents: int | None = None
    list_date: date | None = None
    close_date: date | None = None
    close_price_cents: int | None = None
    mls_number: str | None = None

    listing_agent_name: str | None = None
    listing_agent_phone: str | None = None
    listing_agent_email: str | None = None
    listing_office_name: str | None = None

    remarks_public: str | None = None
    remarks_private: str | None = None

    photos: list[PhotoDTO] = Field(default_factory=list)

    # Raw vendor payload, kept verbatim
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime


class ListingIngestDTO(BaseModel):
    """After normalization: what the pipeline commits to Postgres."""

    model_config = ConfigDict(frozen=True)

    address_hash: str
    raw: RawListing
