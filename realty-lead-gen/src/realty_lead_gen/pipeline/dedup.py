"""Deduplication — find an existing Property row for a normalized listing.

Primary key: address_hash. Fallback (deferred): postal_code + APN, or
geo-nearest-within-25m. When neither matches, the caller creates a
new Property row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from realty_lead_gen.models.property import Property

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


async def find_existing_property_id(
    session: AsyncSession,
    address_hash: str,
) -> uuid.UUID | None:
    stmt = select(Property.id).where(Property.address_hash == address_hash)
    result = await session.execute(stmt)
    row = result.first()
    return row[0] if row else None
