"""tenacity wrappers for consistent retry policy across adapters."""

from __future__ import annotations

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)


class TransientError(Exception):
    """Raised by adapters when a request looks retryable."""


class PermanentError(Exception):
    """Raised by adapters when we should NOT retry (auth, 4xx client).

    Self-describing by contract: `raise_for_vendor_status` puts the vendor,
    the status code, and a bounded excerpt of the response body into the
    message, so a handler can log it without a traceback — the stack would
    only point back at the `raise` two frames up. `TransientError` is
    different: it is retried, and only surfaces after `default_retry` gives
    up, at which point a traceback is worth having.

    No handler for either lives in this agent today; both left with the
    service. They are kept as the policy an outbound call added here should
    follow.
    """


def default_retry() -> AsyncRetrying:
    """Standard retry policy: 5 attempts, exponential backoff + jitter."""
    return AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=0.5, max=15),
        retry=retry_if_exception_type(TransientError),
        reraise=True,
    )
