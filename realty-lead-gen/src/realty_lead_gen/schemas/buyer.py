"""Buyer profile + saved search DTOs."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from realty_lead_gen.models.buyer import BuyerReadiness
from realty_lead_gen.models.score import Persona
from realty_lead_gen.utils.jsontypes import JSONDict


class BuyerProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = None
    phone_e164: str | None = Field(default=None, max_length=32)
    target_cities: list[str] = Field(default_factory=list, max_length=50)
    target_postal_codes: list[str] = Field(default_factory=list, max_length=100)
    max_price_cents: int | None = Field(default=None, ge=0)
    min_price_cents: int | None = Field(default=None, ge=0)
    min_bedrooms: int | None = Field(default=None, ge=0, le=20)
    min_bathrooms: Decimal | None = None
    min_living_area_sqft: int | None = Field(default=None, ge=0)
    max_living_area_sqft: int | None = Field(default=None, ge=0)
    property_types: list[str] = Field(default_factory=list)
    must_haves: list[str] = Field(default_factory=list, max_length=20)
    nice_to_haves: list[str] = Field(default_factory=list, max_length=20)
    deal_breakers: list[str] = Field(default_factory=list, max_length=20)
    readiness: BuyerReadiness = BuyerReadiness.unknown

    @field_validator("target_postal_codes")
    @classmethod
    def _valid_postal(cls, v: list[str]) -> list[str]:
        for z in v:
            if len(z) not in (5, 10) or not z.replace("-", "").isdigit():
                raise ValueError(f"invalid US postal code: {z}")
        return v


class BuyerProfileDTO(BuyerProfileCreate):
    id: uuid.UUID
    is_active: bool


class SavedSearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    persona: Persona
    postal_codes: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    min_score: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    criteria: JSONDict = Field(default_factory=dict)


class SavedSearchDTO(SavedSearchCreate):
    id: uuid.UUID
    is_active: bool
