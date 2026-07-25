from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from realty_lead_gen.api.pagination import LeadCursor


@pytest.mark.unit
class TestLeadCursor:
    def test_roundtrip(self) -> None:
        c = LeadCursor(score=Decimal("0.7521"), id=uuid.uuid4())
        assert LeadCursor.decode(c.encode()) == c

    def test_encoded_is_url_safe(self) -> None:
        c = LeadCursor(score=Decimal("0.5"), id=uuid.uuid4())
        s = c.encode()
        assert "/" not in s and "+" not in s and "=" not in s
