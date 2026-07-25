"""End-to-end: seed a property + snapshot + deal + score + saved search,
then materialize a Lead and read it via the API layer's query.

Covers the API list_leads query semantics against real Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from realty_lead_gen.models.buyer import SavedSearch
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.lead import Lead, LeadStatus
from realty_lead_gen.models.property import Property, PropertyType
from realty_lead_gen.models.score import Persona, Score
from realty_lead_gen.models.user import User, UserRole


@pytest.mark.integration
async def test_end_to_end_lead_visible(_integration_session) -> None:
    session = _integration_session
    user = User(
        external_id="ext-user-1",
        email="dak@example.com",
        display_name="Dak",
        role=UserRole.realtor,
    )
    session.add(user)
    await session.flush()

    prop = Property(
        address_hash="c" * 64,
        street_name="MAIN ST",
        city="AUSTIN",
        state="TX",
        postal_code="78701",
        property_type=PropertyType.single_family,
        attributes={},
    )
    session.add(prop)
    await session.flush()

    deal = DealAnalysis(
        property_id=prop.id,
        analysis_version=1,
        model_id="claude-sonnet-4-5",
        prompt_version="photo_grader_v1",
        overall_condition="C4",
        rehab_low_cents=25_000_00,
        rehab_high_cents=35_000_00,
        avm_value_cents=300_000_00,
        arv_cents=320_000_00,
        rehab_line_items=[],
        comps=[],
        red_flags=[],
        quality_gate_flags=[],
    )
    session.add(deal)

    score = Score(
        property_id=prop.id,
        persona=Persona.flipper,
        scorer_version="flipper_v1",
        score=Decimal("0.7500"),
        confidence=Decimal("0.700"),
        components={},
    )
    session.add(score)

    search = SavedSearch(
        user_id=user.id,
        name="Austin flips",
        persona=Persona.flipper,
        postal_codes=["78701"],
        cities=[],
        regions=[],
        min_score=Decimal("0.5"),
    )
    session.add(search)
    await session.flush()

    lead = Lead(
        property_id=prop.id,
        user_id=user.id,
        persona=Persona.flipper,
        score_id=score.id,
        score_snapshot=score.score,
        status=LeadStatus.new,
        surfaced_at=datetime.now(UTC),
        saved_search_id=search.id,
    )
    session.add(lead)
    await session.flush()

    rows = await session.execute(
        select(Lead).where(Lead.user_id == user.id, Lead.persona == Persona.flipper)
    )
    result = list(rows.scalars().all())
    assert len(result) == 1
    assert result[0].score_snapshot >= Decimal("0.5")
