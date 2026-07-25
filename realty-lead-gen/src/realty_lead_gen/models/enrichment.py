"""Enrichment runs — one row per enrichment execution against a property.

We track *every* enrichment attempt (success or failure), which powers
retries, cost accounting, and per-vendor SLA visibility.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict


class EnrichmentKind(StrEnum):
    photo_grading = "photo_grading"
    avm_valuation = "avm_valuation"
    comps = "comps"
    skip_trace = "skip_trace"
    signals = "signals"
    deal_analysis = "deal_analysis"
    comp_reranking = "comp_reranking"


class RunStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped_budget = "skipped_budget"
    skipped_provider_missing = "skipped_provider_missing"


class EnrichmentRun(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "enrichment_run"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[EnrichmentKind] = mapped_column(
        Enum(EnrichmentKind, name="enrichment_kind", create_type=True),
        nullable=False,
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="enrichment_run_status", create_type=True),
        nullable=False,
        default=RunStatus.running,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd_micros: Mapped[int | None] = mapped_column(Integer)

    input_summary: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)
    output_summary: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(String(2048))

    __table_args__ = (
        Index("ix_enrichment_run__property_kind_started", "property_id", "kind", "started_at"),
        Index("uq_enrichment_run__idempotency_key", "idempotency_key", unique=True),
    )
