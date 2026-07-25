"""ORM -> DTO mappers.

Kept out of the route modules so that the wire shape of an entity is
defined in exactly one place. If two endpoints render the same entity
differently, that is a bug, and having one function makes it a visible one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from realty_lead_gen.schemas.property import PropertyDTO
from realty_lead_gen.utils.addr import NormalizedAddress, compose_display_address
from realty_lead_gen.utils.geo import to_lat_lng

if TYPE_CHECKING:
    from realty_lead_gen.models.property import Property


def property_to_dto(p: Property) -> PropertyDTO:
    display = compose_display_address(
        NormalizedAddress(
            street_number=p.street_number or "",
            street_name=p.street_name,
            unit=p.unit or "",
            city=p.city,
            state=p.state,
            postal_code=p.postal_code,
        )
    )
    coords = to_lat_lng(p.location)
    return PropertyDTO(
        id=p.id,
        address_hash=p.address_hash,
        display_address=display,
        street_number=p.street_number,
        street_name=p.street_name,
        unit=p.unit,
        city=p.city,
        state=p.state,
        postal_code=p.postal_code,
        county_fips=p.county_fips,
        apn=p.apn,
        latitude=coords.latitude if coords else None,
        longitude=coords.longitude if coords else None,
        property_type=p.property_type,
        year_built=p.year_built,
        lot_size_sqft=p.lot_size_sqft,
        living_area_sqft=p.living_area_sqft,
        bedrooms=p.bedrooms,
        bathrooms=p.bathrooms,
        stories=p.stories,
        garage_spaces=p.garage_spaces,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )
