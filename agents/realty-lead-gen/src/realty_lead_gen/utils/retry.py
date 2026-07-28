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
    message. That is why the handlers that catch it log with `logger.error`
    and a `# noqa: TRY400` rather than `logger.exception` — the traceback
    would only point back at the `raise` two frames up, and attaching one to
    every routine "this vendor has no data for this address" would bury the
    real stack traces in the same log stream. `TransientError` is different:
    it is retried, and only surfaces after `default_retry` gives up, at
    which point `logger.exception` *is* correct.
    """


def default_retry() -> AsyncRetrying:
    """Standard retry policy: 5 attempts, exponential backoff + jitter."""
    return AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=0.5, max=15),
        retry=retry_if_exception_type(TransientError),
        reraise=True,
    )
