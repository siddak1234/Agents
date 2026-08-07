"""Per-request time budget derived from agentcall `deadline_ms`.

Sequential outbound calls (Tavily, Nominatim, Groq) share one clock: each
call takes a slice of whatever time is left so a capability with 2-3
searches cannot schedule 15s x N against a 30s deadline.
"""

from __future__ import annotations

import time

# Matches the README example and keeps multi-call capabilities inside a
# typical orchestrator kill window when the caller omits deadline_ms.
DEFAULT_DEADLINE_MS = 30_000
# Hard ceiling per individual HTTP call — never let one hung upstream
# consume the whole budget even when the deadline is generous.
MAX_CALL_TIMEOUT_S = 12.0
MIN_CALL_TIMEOUT_S = 0.5


class DeadlineBudget:
    """Monotonic countdown for the outbound calls of one capability."""

    def __init__(self, deadline_s: float, *, started: float | None = None) -> None:
        self._deadline_s = deadline_s
        self._started = time.monotonic() if started is None else started

    @classmethod
    def from_deadline_ms(cls, deadline_ms: object) -> DeadlineBudget:
        if deadline_ms is None:
            ms = float(DEFAULT_DEADLINE_MS)
        elif isinstance(deadline_ms, bool) or not isinstance(deadline_ms, (int, float)) or deadline_ms <= 0:
            raise ValueError("'deadline_ms' must be a positive number")
        else:
            ms = float(deadline_ms)
        return cls(ms / 1000.0)

    def remaining(self) -> float:
        return max(0.0, self._deadline_s - (time.monotonic() - self._started))

    def for_call(self, calls_left: int) -> float:
        """Timeout in seconds for the next of `calls_left` sequential calls."""
        if calls_left < 1:
            raise ValueError("calls_left must be >= 1")
        rem = self.remaining()
        if rem < MIN_CALL_TIMEOUT_S:
            raise TimeoutError("deadline_ms budget exhausted before outbound call")
        # Leave a small cushion so the last call can still raise TimeoutError
        # cleanly rather than racing the orchestrator's process kill.
        share = (rem * 0.95) / calls_left
        return max(MIN_CALL_TIMEOUT_S, min(MAX_CALL_TIMEOUT_S, share))
