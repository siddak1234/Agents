"""Cursor pagination — opaque, base64-encoded key set.

Offset pagination becomes O(n) on large tables. Cursor pagination
stays O(log n) at the cost of not being able to jump to page N.

The cursor for lead lists is `(score_snapshot DESC, id DESC)`.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LeadCursor:
    score: Decimal
    id: uuid.UUID

    def encode(self) -> str:
        payload = json.dumps({"s": str(self.score), "i": str(self.id)})
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, cursor: str) -> LeadCursor:
        """Parse a cursor, or raise `ValueError` if it is not one.

        The cursor is opaque to clients, which means every truncated URL,
        stale bookmark, hand-edited query string and double-percent-decoding
        client arrives here as arbitrary text. Four layers can reject it and
        each has its own exception type -- `binascii.Error` from base64,
        `UnicodeDecodeError` and `json.JSONDecodeError` from the payload,
        `KeyError`/`TypeError` from the shape, `decimal.InvalidOperation`
        (an `ArithmeticError`, and so *not* a `ValueError`) and another
        `ValueError` from the two field parses.

        Collapsing all of them into one `ValueError` is what lets the caller
        answer with a single 400. Without it the route would have to name
        five exception types to avoid turning a bad link into a 500, and
        would silently regain that 500 the day a field type changed.
        """
        try:
            pad = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + pad).decode())
            return cls(score=Decimal(payload["s"]), id=uuid.UUID(payload["i"]))
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            raise ValueError("malformed cursor") from exc
