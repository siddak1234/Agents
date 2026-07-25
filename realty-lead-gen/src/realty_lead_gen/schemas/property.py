"""Property DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from realty_lead_gen.models.property import PropertyType


class PropertyDTO(BaseModel):
    """Canonical property representation used by the API."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    address_hash: str
    display_address: str
    street_number: str | None
    street_name: str
    unit: str | None
    city: str
    state: str
    postal_code: str
    county_fips: str | None
    apn: str | None

    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)

    property_type: PropertyType
    year_built: int | None
    lot_size_sqft: int | None
    living_area_sqft: int | None
    bedrooms: int | None
    bathrooms: Decimal | None
    stories: int | None
    garage_spaces: int | None

    created_at: datetime
    updated_at: datetime
