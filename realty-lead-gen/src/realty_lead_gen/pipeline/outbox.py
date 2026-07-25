"""Transactional outbox — emitter and relay.

The emitter inserts an ``OutboxEvent`` in the *same* transaction as the
state change it describes, so there is no window in which a lead exists
but its notification was lost (or vice versa). The relay then drains
pending events out-of-band.

Delivery is at-least-once, never exactly-once — that is not achievable
across a process boundary without cooperation from the receiver. Every
event therefore carries a stable ``event_id`` and the consumer is expected
to be idempotent on it; this contract is documented for the frontend in
README.md rather than left implicit.

Pattern reference: Chris Richardson, microservices.io/patterns/data/transactional-outbox.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import httpx
from sqlalchemy import select

from realty_lead_gen.logging import get_logger
from realty_lead_gen.models.outbox import OutboxEvent, OutboxStatus

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

#: Attempt schedule, in seconds. Index N is the wait before attempt N+1.
#: Capped rather than unbounded — an event that has failed nine times is
#: almost certainly failing for a reason that another hour will not fix,
#: and we would rather it retry hourly forever than silently stop.
_BACKOFF_SECONDS: Final[tuple[int, ...]] = (
    5,
    30,
    120,
    600,
    1_800,
    3_600,
)
#: After this many attempts the event is parked in ``failed`` for a human.
#: It is never deleted — the row is the audit record that we tried.
MAX_ATTEMPTS: Final[int] = 12


def emit_event(
    session: AsyncSession,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    """Stage an event for relay. Caller's transaction owns the commit."""
    ev = OutboxEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        status=OutboxStatus.pending,
        dispatch_after=datetime.now(UTC),
    )
    session.add(ev)
    return ev


def backoff_for(attempts: int) -> timedelta:
    """Wait before the next attempt, given how many have already failed."""
    index = min(max(attempts, 0), len(_BACKOFF_SECONDS) - 1)
    return timedelta(seconds=_BACKOFF_SECONDS[index])


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    """HMAC-SHA256 over ``timestamp.body``.

    The timestamp is inside the signed material so a captured delivery
    cannot be replayed later against a receiver that enforces freshness.
    """
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    )
    return f"sha256={mac.hexdigest()}"


def serialize(event: OutboxEvent) -> dict[str, Any]:
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "occurred_at": event.created_at.isoformat() if event.created_at else None,
        "payload": event.payload,
    }


async def claim_batch(
    session: AsyncSession,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> list[OutboxEvent]:
    """Lock a batch of due events for this worker only.

    ``FOR UPDATE SKIP LOCKED`` is what makes the relay horizontally
    scalable: N workers can drain the same table concurrently and each
    one silently steps over rows another already holds, with no
    coordination and no lost events.
    """
    stamp = now or datetime.now(UTC)
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.status == OutboxStatus.pending,
            OutboxEvent.dispatch_after <= stamp,
        )
        .order_by(OutboxEvent.dispatch_after)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


def record_success(event: OutboxEvent, *, now: datetime | None = None) -> None:
    event.status = OutboxStatus.dispatched
    event.dispatched_at = now or datetime.now(UTC)
    event.attempts += 1
    event.last_error = None


def record_failure(
    event: OutboxEvent,
    error: str,
    *,
    now: datetime | None = None,
) -> None:
    stamp = now or datetime.now(UTC)
    event.attempts += 1
    event.last_error = error[:2048]
    if event.attempts >= MAX_ATTEMPTS:
        event.status = OutboxStatus.failed
        event.dispatch_after = None
        logger.error(
            "outbox.parked",
            event_id=str(event.id),
            event_type=event.event_type,
            attempts=event.attempts,
        )
    else:
        event.status = OutboxStatus.pending
        event.dispatch_after = stamp + backoff_for(event.attempts)


class WebhookDispatcher:
    """POSTs one event to the configured webhook, signed.

    Self-disables when no webhook is configured, matching how every source
    adapter behaves: a partially-configured deployment is a supported state,
    not an error. With no webhook set the relay drains events to the log
    and marks them dispatched, so the table cannot grow without bound in a
    frontend-less deployment.
    """

    def __init__(
        self,
        url: str | None,
        secret: str | None,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._secret = secret
        self._timeout = timeout_seconds
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self._url)

    async def dispatch(self, event: OutboxEvent) -> None:
        """Deliver, or raise so the caller records a retryable failure."""
        body_dict = serialize(event)
        if not self._url:
            logger.info("outbox.no_sink", event_id=str(event.id), **_log_fields(event))
            return

        body = json.dumps(body_dict, separators=(",", ":"), default=str).encode("utf-8")
        timestamp = str(int((event.created_at or datetime.now(UTC)).timestamp()))
        headers = {
            "content-type": "application/json",
            "x-rlg-event-id": str(event.id),
            "x-rlg-event-type": event.event_type,
            "x-rlg-timestamp": timestamp,
            # Idempotency-Key is the standard header name receivers already
            # understand; event-id is the same value under our own prefix.
            "idempotency-key": str(event.id),
        }
        if self._secret:
            headers["x-rlg-signature"] = sign_payload(self._secret, body, timestamp)

        client = self._client
        if client is not None:
            response = await client.post(self._url, content=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as owned:
                response = await owned.post(self._url, content=body, headers=headers)
        response.raise_for_status()


def _log_fields(event: OutboxEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
    }


def lead_surfaced_payload(
    *,
    lead_id: uuid.UUID,
    property_id: uuid.UUID,
    user_external_id: str,
    persona: str,
    score: Any,
    scorer_version: str,
) -> dict[str, Any]:
    """Wire payload for ``lead.surfaced`` — the frontend's push signal."""
    return {
        "lead_id": str(lead_id),
        "property_id": str(property_id),
        "user_external_id": user_external_id,
        "persona": persona,
        "score": str(score),
        "scorer_version": scorer_version,
    }
