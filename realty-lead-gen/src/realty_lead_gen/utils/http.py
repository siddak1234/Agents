"""Shared status-code triage for outbound vendor HTTP calls.

Three adapters — RapidAPI/Zillow, RentCast valuation, RentCast comps — each
grew their own copy of "which status codes are worth retrying", and the
copies had already drifted: one gave 429 and 5xx separate branches, another
merged them, one classified 401/403 as auth failures and another lumped them
into the generic 4xx bucket. Since `default_retry` only retries
`TransientError`, that drift *is* the retry policy — a status classified
wrong in one adapter silently gets a different number of attempts than the
same status in the next adapter.

Centralizing it makes the contract one thing that is true everywhere, and a
new adapter inherits it rather than re-deriving it. Codes are named through
`http.HTTPStatus` rather than as bare integers so the intent is legible at
the call site and the numbers cannot drift from their meanings.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

# Runtime import, not TYPE_CHECKING: `httpx.ResponseNotRead` is caught in an
# `except` clause below, which is evaluated at runtime.
import httpx

from realty_lead_gen.utils.retry import PermanentError, TransientError

#: 4xx codes that are nonetheless worth another attempt. Everything else in
#: the 4xx range describes a request that will be malformed in exactly the
#: same way next time, so retrying only burns the rate-limit budget.
_RETRYABLE_CLIENT_ERRORS: Final[frozenset[int]] = frozenset(
    {
        HTTPStatus.REQUEST_TIMEOUT,  # 408 — server gave up waiting, not our bug
        HTTPStatus.TOO_EARLY,  # 425 — replay risk; the retry is the fix
        HTTPStatus.TOO_MANY_REQUESTS,  # 429 — backoff is precisely the remedy
    }
)

#: 5xx codes that are *not* worth retrying. The 5xx range is retryable as a
#: rule because it means "my fault, not yours", but these two are stable
#: statements about the server's capabilities: the same request will get the
#: same answer on attempt five as on attempt one.
_PERMANENT_SERVER_ERRORS: Final[frozenset[int]] = frozenset(
    {
        HTTPStatus.NOT_IMPLEMENTED,  # 501
        HTTPStatus.HTTP_VERSION_NOT_SUPPORTED,  # 505
    }
)

#: Codes that mean the credential is missing, wrong, expired, or out of quota.
#: Split out from the generic permanent bucket only so the raised message
#: names the actual problem — an operator reading "auth failed: 403" reaches
#: for the API key, where "client error 403" sends them to read the request.
_AUTH_ERRORS: Final[frozenset[int]] = frozenset(
    {
        HTTPStatus.UNAUTHORIZED,  # 401
        HTTPStatus.PAYMENT_REQUIRED,  # 402 — how several vendors signal "plan exhausted"
        HTTPStatus.FORBIDDEN,  # 403
    }
)

#: First status code past the 5xx range. `HTTPStatus` has no "max server
#: error" member, and an open-ended `>= 500` would swallow the nonstandard
#: 6xx codes some gateways emit, so the ceiling is named once here.
_SERVER_ERROR_CEILING: Final[int] = 600

#: How much of an error body to carry into the exception message. Enough to
#: identify the vendor's error code, short enough that it cannot dump a
#: paginated payload (or a page of someone's PII) into the logs.
_BODY_EXCERPT_CHARS: Final[int] = 200


def is_transient_status(code: int) -> bool:
    """True if `code` is worth another attempt.

    Split out from `raise_for_vendor_status` because not every caller holds
    an `httpx.Response`: the Anthropic SDK surfaces failures as
    `APIStatusError`, which carries a bare integer. Both paths asking the
    same function is what keeps "429 is retryable" from being true of the
    vendor adapters and false of the LLM client.
    """
    if code in _RETRYABLE_CLIENT_ERRORS:
        return True
    return (
        HTTPStatus.INTERNAL_SERVER_ERROR <= code < _SERVER_ERROR_CEILING
        and code not in _PERMANENT_SERVER_ERRORS
    )


def raise_for_vendor_status(resp: httpx.Response, *, vendor: str) -> None:
    """Translate an HTTP status into this codebase's retry contract.

    Returns normally for any non-error status. Raises `TransientError` for
    statuses that a later attempt could plausibly succeed on — which is the
    only exception type `default_retry` retries — and `PermanentError` for
    statuses that will fail identically every time.

    `vendor` is carried into the message so a failure in a stack of adapters
    identifies itself without the caller having to re-wrap the exception.
    """
    code = resp.status_code

    if is_transient_status(code):
        raise TransientError(f"{vendor} {code}: {_excerpt(resp)}")

    if code in _AUTH_ERRORS:
        raise PermanentError(f"{vendor} auth failed: {code}")

    # `>= BAD_REQUEST` rather than httpx's `is_error` (which stops at 599) so
    # a nonstandard 6xx from a misconfigured gateway still fails loudly
    # instead of falling through to `resp.json()` and erroring somewhere less
    # informative.
    if code >= HTTPStatus.BAD_REQUEST:
        raise PermanentError(f"{vendor} {code}: {_excerpt(resp)}")


def _excerpt(resp: httpx.Response) -> str:
    """A bounded, decode-safe slice of the response body for error messages."""
    try:
        return resp.text[:_BODY_EXCERPT_CHARS]
    except httpx.ResponseNotRead:
        # `.text` is safe on binary bodies — httpx decodes with
        # `errors="replace"`, verified against a `\xff\xfe` payload — but it
        # raises on a *streamed* response nobody has read yet. The status code
        # is the useful part anyway; losing the excerpt must not turn a clean
        # PermanentError into an unrelated crash inside the error path.
        return "<unread stream>"
