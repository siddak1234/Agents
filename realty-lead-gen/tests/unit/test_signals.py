"""Derived signal detection."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from realty_lead_gen.enrichment.signals import SignalDetectionStep
from realty_lead_gen.models.property import PropertySnapshot, SnapshotSource
from realty_lead_gen.models.signal import SignalKind


def _snap(observed_at: datetime, **kw) -> PropertySnapshot:
    return PropertySnapshot(
        id=uuid.uuid4(),
        property_id=uuid.uuid4(),
        source=SnapshotSource.mls_reso,
        source_record_id="abc",
        observed_at=observed_at,
        raw_payload={},
        **kw,
    )


@pytest.mark.unit
class TestSignalDetection:
    def test_aged_listing_signal(self) -> None:
        now = datetime.now(UTC)
        snap = _snap(now, days_on_market=120, list_price_cents=350_000_00)
        signals = SignalDetectionStep().compute([snap], as_of=now)
        assert any(s.kind == SignalKind.aged_listing for s in signals)

    def test_no_signals_for_fresh_active(self) -> None:
        now = datetime.now(UTC)
        snap = _snap(now, days_on_market=10, list_price_cents=350_000_00, status="active")
        signals = SignalDetectionStep().compute([snap], as_of=now)
        aged = [s for s in signals if s.kind == SignalKind.aged_listing]
        assert not aged

    def test_price_cut(self) -> None:
        base = datetime.now(UTC)
        s1 = _snap(base - timedelta(days=30), list_price_cents=400_000_00)
        s2 = _snap(base, list_price_cents=370_000_00)
        signals = SignalDetectionStep().compute([s1, s2], as_of=base)
        assert any(s.kind == SignalKind.recent_price_cut for s in signals)

    def test_long_term_ownership(self) -> None:
        now = datetime.now(UTC)
        snap = _snap(
            now,
            list_price_cents=350_000_00,
            last_sold_date=date(now.year - 20, 1, 1),
        )
        signals = SignalDetectionStep().compute([snap], as_of=now)
        assert any(s.kind == SignalKind.long_term_ownership for s in signals)
