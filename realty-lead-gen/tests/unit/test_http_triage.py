"""Vendor HTTP status triage — the retry contract, stated exhaustively.

`utils/http.py` is not a helper, it *is* the retry policy: `default_retry`
retries `TransientError` and nothing else, so whichever exception
`raise_for_vendor_status` picks decides whether a call gets one attempt or
five. That makes a misclassified status a silent behaviour change across
every adapter at once — a 503 demoted to `PermanentError` turns a routine
vendor blip into a dropped enrichment, and a 400 promoted to
`TransientError` burns five calls of a metered quota re-sending a request
that was malformed the first time.

Two decisions about how this file is written, both deliberate:

* **The expected classification is re-derived from bare integers here**
  (`_expected_transient` below) rather than imported from the module's own
  frozensets. Importing them would make the test a tautology — it would
  pass just as happily after someone moved 503 into
  `_PERMANENT_SERVER_ERRORS`. Writing the numbers out a second time is the
  only way the test can disagree with the implementation.
* **The sweep is exhaustive over 100-699** rather than a sampled table.
  The whole surface is 600 integers; there is no reason to guess which
  ones matter, and the range covers the nonstandard 6xx gateways emit.
"""

from __future__ import annotations

import httpx
import pytest
from tenacity import wait_none

from realty_lead_gen.utils.http import (
    _AUTH_ERRORS,
    _BODY_EXCERPT_CHARS,
    _PERMANENT_SERVER_ERRORS,
    _RETRYABLE_CLIENT_ERRORS,
    _excerpt,
    is_transient_status,
    raise_for_vendor_status,
)
from realty_lead_gen.utils.retry import PermanentError, TransientError, default_retry

pytestmark = pytest.mark.unit

#: Widest range worth asserting over: every real status plus the
#: nonstandard 6xx band `raise_for_vendor_status` explicitly handles.
_ALL_CODES = range(100, 700)

_VENDOR = "rentcast"


def _expected_transient(code: int) -> bool:
    """The retry contract, restated independently of the implementation.

    Bare integers on purpose — see the module docstring. If this and
    `is_transient_status` ever disagree, one of them is a bug and the sweep
    below will say which code.
    """
    if code in {408, 425, 429}:
        return True
    return 500 <= code <= 599 and code not in {501, 505}


def _response(code: int, body: str = "vendor said no") -> httpx.Response:
    return httpx.Response(code, content=body.encode())


# --- classification ----------------------------------------------------------


def test_is_transient_status_matches_the_contract_over_every_code() -> None:
    disagreements = [
        code for code in _ALL_CODES if is_transient_status(code) is not _expected_transient(code)
    ]
    assert disagreements == []


def test_raise_for_vendor_status_never_disagrees_with_is_transient_status() -> None:
    """The two entry points must classify identically.

    `is_transient_status` exists because the Anthropic SDK reports failures
    as an `APIStatusError` carrying a bare int, with no `httpx.Response` to
    hand — so the LLM client and the vendor adapters reach the same policy
    through different doors. If those doors ever diverge, "429 is
    retryable" becomes true of one call path and false of the other, which
    is exactly the drift this module was written to end.
    """
    for code in _ALL_CODES:
        raised: BaseException | None = None
        try:
            raise_for_vendor_status(_response(code), vendor=_VENDOR)
        except (TransientError, PermanentError) as exc:
            raised = exc

        if is_transient_status(code):
            assert isinstance(raised, TransientError), f"{code} should be transient"
        elif code >= 400:
            assert isinstance(raised, PermanentError), f"{code} should be permanent"
        else:
            assert raised is None, f"{code} is not an error status"


def test_non_error_statuses_return_quietly() -> None:
    # 1xx/2xx/3xx all fall through: the caller goes on to read the body.
    for code in (100, 200, 201, 204, 301, 304, 399):
        assert raise_for_vendor_status(_response(code), vendor=_VENDOR) is None


def test_permanent_server_errors_are_the_only_5xx_that_do_not_retry() -> None:
    for code in (501, 505):
        with pytest.raises(PermanentError):
            raise_for_vendor_status(_response(code), vendor=_VENDOR)
    for code in (500, 502, 503, 504, 507, 599):
        with pytest.raises(TransientError):
            raise_for_vendor_status(_response(code), vendor=_VENDOR)


def test_nonstandard_6xx_fails_loudly_instead_of_falling_through() -> None:
    """A misconfigured gateway's 6xx must not reach `resp.json()`.

    httpx's own `is_error` stops at 599, so the module uses
    `>= BAD_REQUEST` instead. Without that, a 600 would return normally and
    the failure would surface as a JSON decode error somewhere with no
    vendor name attached.
    """
    for code in (600, 699):
        assert not is_transient_status(code)
        with pytest.raises(PermanentError, match=str(code)):
            raise_for_vendor_status(_response(code), vendor=_VENDOR)


# --- message shape -----------------------------------------------------------


def test_transient_message_names_vendor_code_and_body() -> None:
    with pytest.raises(TransientError) as excinfo:
        raise_for_vendor_status(_response(429, "rate limit exceeded"), vendor=_VENDOR)
    assert str(excinfo.value) == "rentcast 429: rate limit exceeded"


def test_auth_failures_name_the_credential_and_withhold_the_body() -> None:
    """401/402/403 say "auth failed" and deliberately drop the excerpt.

    Two reasons, and the second is why this is asserted rather than left to
    read as a formatting choice: an operator who sees "auth failed: 403"
    reaches for the API key, where "client error 403" sends them to re-read
    the request; and an auth-rejection body is the one place a vendor is
    most likely to echo the credential it just refused, which must not land
    in an exception message that gets logged.
    """
    for code in (401, 402, 403):
        with pytest.raises(PermanentError) as excinfo:
            raise_for_vendor_status(
                _response(code, "invalid key: sk-live-DEADBEEF"), vendor=_VENDOR
            )
        message = str(excinfo.value)
        assert message == f"rentcast auth failed: {code}"
        assert "sk-live-DEADBEEF" not in message


def test_ordinary_client_errors_carry_the_body_for_diagnosis() -> None:
    with pytest.raises(PermanentError) as excinfo:
        raise_for_vendor_status(_response(422, "address failed validation"), vendor=_VENDOR)
    assert str(excinfo.value) == "rentcast 422: address failed validation"


def test_error_messages_stay_bounded_even_for_huge_bodies() -> None:
    """A paginated payload must not become a log line.

    The bound is what makes it safe for the `PermanentError` handlers to log
    the message directly; without it, one vendor returning an HTML error
    page turns every failed enrichment into kilobytes of log.
    """
    with pytest.raises(PermanentError) as excinfo:
        raise_for_vendor_status(_response(400, "x" * 10_000), vendor=_VENDOR)
    message = str(excinfo.value)
    assert message.count("x") == _BODY_EXCERPT_CHARS
    assert len(message) < _BODY_EXCERPT_CHARS + 50


# --- excerpting --------------------------------------------------------------


def test_excerpt_truncates_at_the_documented_bound() -> None:
    assert _excerpt(_response(500, "y" * 1000)) == "y" * _BODY_EXCERPT_CHARS
    # Bodies under the bound are passed through whole, not padded or elided.
    assert _excerpt(_response(500, "short")) == "short"
    assert _excerpt(_response(500, "")) == ""


def test_excerpt_survives_an_undecodable_body() -> None:
    """The error path must not raise its own error.

    httpx decodes with `errors="replace"`, so binary garbage degrades to
    replacement characters rather than a `UnicodeDecodeError` thrown from
    inside the construction of a different exception's message.
    """
    resp = httpx.Response(500, content=b"\xff\xfe\x00not utf-8")
    excerpt = _excerpt(resp)
    assert "not utf-8" in excerpt

    with pytest.raises(TransientError):
        raise_for_vendor_status(resp, vendor=_VENDOR)


def test_excerpt_on_an_unread_stream_degrades_instead_of_raising() -> None:
    """A streamed response nobody has read yet still classifies.

    `.text` raises `ResponseNotRead` here — and this is the realistic case
    for any adapter that switches to `client.stream(...)`. Losing the
    excerpt is acceptable; losing the status classification is not.
    """
    streamed = httpx.Response(503, content=iter([b"chunked"]))
    assert not streamed.is_stream_consumed
    assert _excerpt(streamed) == "<unread stream>"

    with pytest.raises(TransientError) as excinfo:
        raise_for_vendor_status(streamed, vendor=_VENDOR)
    assert str(excinfo.value) == "rentcast 503: <unread stream>"


# --- the buckets themselves --------------------------------------------------


def test_status_buckets_are_disjoint_and_in_range() -> None:
    """No code may be claimed by two buckets.

    `raise_for_vendor_status` checks transient first, so a code in both
    `_RETRYABLE_CLIENT_ERRORS` and `_AUTH_ERRORS` would make the auth branch
    unreachable for it — a dead branch that still looks correct when read.
    """
    assert not _RETRYABLE_CLIENT_ERRORS & _AUTH_ERRORS
    assert not _RETRYABLE_CLIENT_ERRORS & _PERMANENT_SERVER_ERRORS
    assert not _AUTH_ERRORS & _PERMANENT_SERVER_ERRORS

    assert all(400 <= code <= 499 for code in _RETRYABLE_CLIENT_ERRORS | _AUTH_ERRORS)
    assert all(500 <= code <= 599 for code in _PERMANENT_SERVER_ERRORS)


# --- the policy this feeds ---------------------------------------------------


async def test_default_retry_retries_exactly_what_triage_marks_transient() -> None:
    """Close the loop: the classification only matters via this policy.

    Asserting the exception *type* would prove nothing on its own — it is
    `default_retry`'s `retry_if_exception_type(TransientError)` that turns
    the type into a number of attempts. `wait_none()` strips the backoff so
    the assertion is about attempt count, not elapsed time.
    """
    transient_attempts = 0
    with pytest.raises(TransientError):
        async for attempt in default_retry().copy(wait=wait_none()):
            with attempt:
                transient_attempts += 1
                raise_for_vendor_status(_response(503), vendor=_VENDOR)
    assert transient_attempts == 5

    permanent_attempts = 0
    with pytest.raises(PermanentError):
        async for attempt in default_retry().copy(wait=wait_none()):
            with attempt:
                permanent_attempts += 1
                raise_for_vendor_status(_response(403), vendor=_VENDOR)
    assert permanent_attempts == 1
