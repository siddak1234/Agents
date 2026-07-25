"""Outbox relay job — drains staged events to the configured webhook.

Runs on a short cron. Each pass claims a bounded batch with
``SELECT ... FOR UPDATE SKIP LOCKED`` so multiple workers can drain
concurrently without double-delivering, dispatches each event, and records
success or a backed-off retry inside the same transaction that holds the
lock. If the process dies mid-batch the transaction rolls back, the locks
release, and the events are simply re-claimed on the next pass — nothing
is lost and nothing is stuck.
"""

from __future__ import annotations

from typing import Any

from realty_lead_gen.config import get_settings
from realty_lead_gen.db import session_scope
from realty_lead_gen.logging import get_logger
from realty_lead_gen.pipeline.outbox import (
    WebhookDispatcher,
    claim_batch,
    record_failure,
    record_success,
)

logger = get_logger(__name__)


async def outbox_relay_job(
    ctx: dict[str, Any],
    batch_size: int = 100,
) -> dict[str, int]:
    settings = get_settings()
    dispatcher = WebhookDispatcher(
        url=str(settings.outbox_webhook_url) if settings.outbox_webhook_url else None,
        secret=(
            settings.outbox_webhook_secret.get_secret_value()
            if settings.outbox_webhook_secret
            else None
        ),
        timeout_seconds=settings.outbox_webhook_timeout_seconds,
    )

    dispatched = 0
    failed = 0
    async with session_scope() as session:
        events = await claim_batch(session, limit=batch_size)
        for event in events:
            try:
                await dispatcher.dispatch(event)
            except Exception as exc:
                record_failure(event, f"{type(exc).__name__}: {exc}")
                failed += 1
                logger.warning(
                    "outbox.dispatch_failed",
                    event_id=str(event.id),
                    event_type=event.event_type,
                    attempts=event.attempts,
                    error=str(exc),
                )
            else:
                record_success(event)
                dispatched += 1

    if dispatched or failed:
        logger.info("outbox.relay_pass", dispatched=dispatched, failed=failed)
    return {"dispatched": dispatched, "failed": failed}
