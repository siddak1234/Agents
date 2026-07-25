"""The canonical property + temporal snapshots.

`Property` is the deduped canonical record for a physical parcel — one
row per real-world property regardless of how many MLS listings or
public-record events touch it. Ownership, condition, and any mutable
attribute lives in `PropertySnapshot` so we retain full history.

Deduplication key: `address_hash` (see ``utils.addr.hash_address``). We
use a triple guard — address_hash unique, plus a partial index on
(APN, county_fips), plus a geo index for spatial fallback.

RESO-aligned fields where possible; see the RESO Data Dictionary 2.0
for the canonical vocabulary we mirror. Fields we don't need at MVP
(e.g. HOA subfees) are omitted; extend rather than reshape.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.deal import DealAnalysis
    from realty_lead_gen.models.listing import Listing
    from realty_lead_gen.models.owner import PropertyOwnership
    from realty_lead_gen.models.photo import Photo
    from realty_lead_gen.models.signal import Signal


class PropertyType(StrEnum):
    single_family = "single_family"
    condo = "condo"
    townhouse = "townhouse"
    multi_family_2_4 = "multi_family_2_4"
    multi_family_5_plus = "multi_family_5_plus"
    manufactured = "manufactured"
    land = "land"
    commercial = "commercial"
    other = "other"


class Property(Base, UUIDPKMixin, TimestampMixin):
    """Canonical, deduped real-world property."""

    __tablename__ = "property"

    # --- Identity / dedup ----------------------------------------------------
    # SHA-256 hex over normalized components (street, unit, city, state, zip).
    # See utils.addr.hash_address for the canonical formulation.
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # --- Address (denormalized for query ergonomics) --------------------------
    street_number: Mapped[str | None] = mapped_column(String(16))
    street_name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str] = mapped_column(String(96), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(10), nullable=False)
    county_fips: Mapped[str | None] = mapped_column(String(5))  # e.g. "06037" LA
    apn: Mapped[str | None] = mapped_column(String(64))  # assessor parcel number

    # --- Geo -----------------------------------------------------------------
    # SRID 4326 (WGS84). Geography type handles great-circle distance for us.
    #
    # spatial_index=False is deliberate. GeoAlchemy2 otherwise attaches its
    # own `idx_property_location` GiST index *and* creates it from a DDL
    # event listener, which collides with the identical index Alembic emits
    # from metadata ("relation already exists" on a clean migrate). We
    # declare the index ourselves in __table_args__ below so exactly one
    # thing owns it and its name follows the project convention.
    location = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=False))

    # --- Physical attributes (rarely change, so kept on the parent) ----------
    property_type: Mapped[PropertyType] = mapped_column(
        Enum(PropertyType, name="property_type", create_type=True),
        nullable=False,
        default=PropertyType.single_family,
    )
    year_built: Mapped[int | None] = mapped_column(Integer)
    lot_size_sqft: Mapped[int | None] = mapped_column(Integer)
    living_area_sqft: Mapped[int | None] = mapped_column(Integer)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))  # 2.5 baths
    stories: Mapped[int | None] = mapped_column(Integer)
    garage_spaces: Mapped[int | None] = mapped_column(Integer)

    # --- Free-form attributes (RESO extension bag) ----------------------------
    attributes: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    # --- Relationships -------------------------------------------------------
    listings: Mapped[list[Listing]] = relationship(back_populates="property", lazy="raise")
    photos: Mapped[list[Photo]] = relationship(back_populates="property", lazy="raise")
    signals: Mapped[list[Signal]] = relationship(back_populates="property", lazy="raise")
    ownerships: Mapped[list[PropertyOwnership]] = relationship(
        back_populates="property", lazy="raise"
    )
    deals: Mapped[list[DealAnalysis]] = relationship(back_populates="property", lazy="raise")
    snapshots: Mapped[list[PropertySnapshot]] = relationship(
        back_populates="property", lazy="raise"
    )

    __table_args__ = (
        Index("ix_property__location", "location", postgresql_using="gist"),
        Index("ix_property__city_state", "city", "state"),
        Index("ix_property__postal_code", "postal_code"),
        UniqueConstraint("apn", "county_fips", name="uq_property__apn_county"),
        CheckConstraint("length(state) = 2", name="state_two_chars"),
        CheckConstraint("bedrooms IS NULL OR bedrooms >= 0", name="bedrooms_nonneg"),
        CheckConstraint(
            "living_area_sqft IS NULL OR living_area_sqft >= 0",
            name="living_area_nonneg",
        ),
    )


class SnapshotSource(StrEnum):
    """Where a snapshot came from — dictates trust weight in downstream logic."""

    mls_reso = "mls_reso"
    zillow = "zillow"
    redfin = "redfin"
    realtor_com = "realtor_com"
    propertyradar = "propertyradar"
    attom = "attom"
    county_recorder = "county_recorder"
    county_assessor = "county_assessor"
    fsbo_portal = "fsbo_portal"
    manual = "manual"


class PropertySnapshot(Base, UUIDPKMixin, TimestampMixin):
    """A point-in-time capture of a property's mutable state.

    Every ingest run appends a snapshot (never mutates prior ones), so
    downstream queries can reason about drift and reject stale data.
    """

    __tablename__ = "property_snapshot"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[SnapshotSource] = mapped_column(
        Enum(SnapshotSource, name="snapshot_source", create_type=True),
        nullable=False,
    )
    source_record_id: Mapped[str | None] = mapped_column(String(128))  # foreign id
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- Mutable pricing / status --------------------------------------------
    status: Mapped[str | None] = mapped_column(String(32))  # active, pending, sold, ...
    list_price_cents: Mapped[int | None] = mapped_column(Integer)
    last_sold_price_cents: Mapped[int | None] = mapped_column(Integer)
    last_sold_date: Mapped[date | None] = mapped_column(Date)
    days_on_market: Mapped[int | None] = mapped_column(Integer)
    price_per_sqft_cents: Mapped[int | None] = mapped_column(Integer)
    tax_assessed_value_cents: Mapped[int | None] = mapped_column(Integer)
    tax_annual_cents: Mapped[int | None] = mapped_column(Integer)
    hoa_monthly_cents: Mapped[int | None] = mapped_column(Integer)

    # --- Free-form payload from the source (RAW, unmodified) -----------------
    raw_payload: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)

    property: Mapped[Property] = relationship(back_populates="snapshots", lazy="joined")

    __table_args__ = (
        Index("ix_property_snapshot__property_observed", "property_id", "observed_at"),
        Index("ix_property_snapshot__source_record", "source", "source_record_id"),
        UniqueConstraint(
            "property_id",
            "source",
            "source_record_id",
            "observed_at",
            name="uq_property_snapshot__source_record_observed",
        ),
    )
