"""Lead + deal analysis DTOs for the API surface."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from realty_lead_gen.models.lead import LeadFeedbackAction, LeadStatus
from realty_lead_gen.models.score import Persona
from realty_lead_gen.schemas.property import PropertyDTO


class DealSummaryDTO(BaseModel):
    """Compressed deal analysis for lead lists."""

    model_config = ConfigDict(frozen=True)

    overall_condition: str | None
    rehab_low_cents: int | None
    rehab_high_cents: int | None
    avm_value_cents: int | None
    avm_confidence: Decimal | None
    arv_cents: int | None
    monthly_rent_cents: int | None
    red_flags: list[str] = Field(default_factory=list)


class DealAnalysisDTO(BaseModel):
    """Full deal analysis for lead detail."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    analysis_version: int
    overall_condition: str | None
    condition_confidence: Decimal | None
    rehab_low_cents: int | None
    rehab_high_cents: int | None
    rehab_line_items: list[dict[str, Any]]
    avm_value_cents: int | None
    avm_low_cents: int | None
    avm_high_cents: int | None
    avm_confidence: Decimal | None
    avm_provider: str | None
    arv_cents: int | None
    arv_confidence: Decimal | None
    monthly_rent_cents: int | None
    comps: list[dict[str, Any]]
    comps_narrative: str | None
    red_flags: list[str]
    narrative: str | None
    quality_gate_flags: list[str]


class ScoreDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    persona: Persona
    score: Decimal
    confidence: Decimal
    components: dict[str, Any]
    rationale: str | None


class LeadListItemDTO(BaseModel):
    """One row in the lead list."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    property: PropertyDTO
    persona: Persona
    status: LeadStatus
    score: Decimal
    surfaced_at: datetime
    deal_summary: DealSummaryDTO | None


class LeadDetailDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    property: PropertyDTO
    persona: Persona
    status: LeadStatus
    score: Decimal
    surfaced_at: datetime
    deal: DealAnalysisDTO | None
    score_detail: ScoreDTO | None


class LeadFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: LeadFeedbackAction
    edits: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=2048)


class PaginatedLeadsDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[LeadListItemDTO]
    next_cursor: str | None
    total_estimate: int | None
