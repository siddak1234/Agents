"""Skip-trace adapter — BatchSkipTracing reference implementation stub.

TCPA / DNC compliance is mandatory before any contact channel returned
here can be surfaced for outbound calling. See ARCHITECTURE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SkipTraceResult:
    phones: list[dict[str, Any]]
    emails: list[dict[str, Any]]
    mailing_addresses: list[dict[str, Any]]
    provider: str
    raw: dict[str, Any]


class SkipTraceStep:
    provider = "batchskiptracing"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def available(self) -> bool:
        return self._settings.batchskiptracing_api_key is not None

    async def lookup(
        self,
        first_name: str | None,
        last_name: str | None,
        mailing_address: str | None,
    ) -> SkipTraceResult | None:
        if not self.available:
            return None
        # Production implementation: POST v1/skip/person with normalized
        # inputs. Rate-limit at 5 rps per key (BatchData default).
        # Every returned phone should be scrubbed against DNC + reassigned
        # number database before we mark rpc_probability > 0 for outbound
        # dialing (see contact channel model).
        logger.warning(
            "skip_trace.not_implemented",
            first_name=first_name,
            last_name=last_name,
            mailing_address=mailing_address,
        )
        return None
