"""Per-subject API rate limiting.

The quota is **per authenticated subject**, not per IP. Every client of this
service reaches it through Snoopy, so IP keying would lump an entire office
behind one NAT into a single bucket while doing nothing to stop a single
compromised token. The token's ``sub`` claim is the only identifier that maps
one-to-one onto "who is spending our budget", and it is the one thing a caller
cannot forge, because it comes out of a signature check.

Why this is not slowapi
-----------------------
`slowapi` is the obvious choice and it is what this project originally
declared. Reading its source (0.1.10) ruled it out on three counts:

1. **It is synchronous.** ``Limiter._check_request_limit`` calls
   ``limits.strategies.*.hit()``, which for a Redis store performs blocking
   socket I/O. Called from an ``async def`` endpoint, that blocks the event
   loop for the duration of the round trip and stalls every other in-flight
   request on the worker. The alternative — its default ``memory://`` store —
   silently multiplies the real limit by the worker count.
2. **Its key function cannot see the authenticated subject.** ``key_func``
   receives only the ``Request``. Auth is a route dependency, so in middleware
   mode the subject does not exist yet; in decorator mode it exists only if
   every endpoint is rewritten to take ``request: Request`` and stash the
   subject on ``request.state`` first.
3. **Its 429 body is** ``{"error": "..."}``, which is not the RFC 7807
   Problem Details shape every other error in this API uses.

So we build directly on :pypi:`limits` — the same library slowapi wraps — in
its ``limits.aio`` form. That is one fewer dependency, not one more, and it
gives us an awaitable ``hit()``.

Design notes
------------
*Strategy*: moving window. Fixed windows let a caller spend the full quota in
the last second of one window and again in the first second of the next, i.e.
2x the nominal rate across a window boundary. A moving window costs one sorted
set per subject in Redis (bounded by the limit itself — 120 entries for the
default 120/minute) and has no boundary burst.

*Storage*: Redis, so the limit is shared across every API worker. The URI is
derived from ``REDIS_URL`` unless ``API_RATE_LIMIT_STORAGE_URI`` overrides it.
``limits`` addresses async backends with an ``async+`` scheme prefix, and its
async Redis storage defaults to the :pypi:`coredis` client, which we do not
install — hence the explicit ``implementation="redispy"`` so it uses the
``redis.asyncio`` client this project already depends on.

*Failure mode*: fail open. If Redis is unreachable the request is allowed and
a warning is logged. A rate limiter is a budget control, not an authorization
control; failing closed would convert a Redis blip into a total outage, which
is a strictly worse incident than briefly serving over quota.

*Scope*: authenticated routers only. ``/healthz`` and ``/readyz`` are
deliberately unlimited so a load balancer's probe never consumes anyone's
budget, and unauthenticated traffic — requests that fail token verification
before ever reaching this dependency — is not covered here at all. Throttling
that is an edge concern (reverse proxy / API gateway / WAF), because by the
time it reaches application code the expensive part has already happened.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from fastapi import Depends, Request, Response
from limits import RateLimitItemPerMinute
from limits.aio.storage import Storage as AsyncStorage
from limits.aio.strategies import MovingWindowRateLimiter
from limits.errors import StorageError
from limits.storage import storage_from_string
from starlette.exceptions import HTTPException as StarletteHTTPException

from realty_lead_gen.api.deps import get_current_user
from realty_lead_gen.api.errors import ProblemResponse
from realty_lead_gen.logging import get_logger

if TYPE_CHECKING:
    from realty_lead_gen.api.auth import TokenClaims
    from realty_lead_gen.config import Settings

logger = get_logger(__name__)

#: Response header names. The ``X-`` trio is what every HTTP client library
#: and dashboard already understands; the limit is the configured ceiling,
#: remaining is what is left in the current window, and reset is whole
#: seconds until the window frees up.
HEADER_LIMIT: Final = "X-RateLimit-Limit"
HEADER_REMAINING: Final = "X-RateLimit-Remaining"
HEADER_RESET: Final = "X-RateLimit-Reset"
HEADER_RETRY_AFTER: Final = "Retry-After"

#: Prefix for every key this limiter writes. `limits` appends the amount and
#: granularity to the key it derives, so raising the configured limit starts a
#: fresh window rather than reinterpreting counts taken under the old ceiling.
KEY_NAMESPACE: Final = "rlg:api"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """The outcome of one quota check."""

    allowed: bool
    limit: int
    remaining: int
    #: Whole seconds until the window frees up. Never negative; at least 1
    #: when the request was denied, because ``Retry-After: 0`` tells a client
    #: to retry immediately, which is exactly what we are trying to stop.
    reset_after: int

    def headers(self) -> dict[str, str]:
        out = {
            HEADER_LIMIT: str(self.limit),
            HEADER_REMAINING: str(self.remaining),
            HEADER_RESET: str(self.reset_after),
        }
        if not self.allowed:
            out[HEADER_RETRY_AFTER] = str(self.reset_after)
        return out


class RateLimitExceeded(StarletteHTTPException):
    """Raised when a subject has spent its quota for the current window.

    Subclasses ``HTTPException`` on purpose. The dedicated handler below is
    what produces the documented response, but if it were ever left
    unregistered the framework's default still returns a 429 rather than
    turning a quota breach into a 500.
    """

    def __init__(self, decision: RateLimitDecision) -> None:
        self.decision = decision
        super().__init__(
            status_code=429,
            detail=f"Rate limit exceeded: {decision.limit} requests per minute.",
            headers=decision.headers(),
        )


class RateLimitGuard:
    """Holds the limiter and answers "may this subject make one more call?".

    Constructing this is cheap and does no I/O: `limits` connects lazily, so
    building the app never depends on Redis being up. That matters because
    ``main.py`` instantiates the app at import time.
    """

    def __init__(self, *, per_minute: int, storage_uri: str) -> None:
        self.per_minute = per_minute
        self.storage_uri = storage_uri
        self._item = RateLimitItemPerMinute(per_minute, 1, namespace=KEY_NAMESPACE)
        storage = storage_from_string(
            storage_uri,
            # Only meaningful for the Redis backends; the memory backend
            # accepts and ignores unknown options.
            implementation="redispy",
            # Collapses every backend-specific driver exception into one
            # `limits` type, so the fail-open path below catches exactly the
            # storage failures and nothing else.
            wrap_exceptions=True,
        )
        # `storage_from_string` returns sync *or* async storage depending on the
        # scheme, and a sync backend here would block the event loop on every
        # request while still passing every test that uses `async+memory://`.
        # `storage_uri_for` already rejects non-async URIs; this is the check
        # that makes that guarantee structural rather than conventional, and it
        # is what lets the rest of this class be typed as awaitable.
        if not isinstance(storage, AsyncStorage):
            msg = (
                f"Rate limit storage {_redact(storage_uri)!r} resolved to the "
                f"synchronous backend {type(storage).__name__}; the URI must use "
                "an 'async+' scheme."
            )
            raise TypeError(msg)
        self._storage: AsyncStorage = storage
        self._limiter = MovingWindowRateLimiter(self._storage)

    async def check(self, subject: str) -> RateLimitDecision | None:
        """Consume one unit of ``subject``'s quota.

        Returns ``None`` when the backing store could not be reached — the
        caller should then let the request through unmetered (see the
        fail-open note in the module docstring).

        Two round trips: ``hit`` to consume, ``get_window_stats`` to report.
        The second is what makes the ``X-RateLimit-*`` headers truthful, and
        it is the same shape slowapi uses. Both are pipelined server-side by
        the Lua scripts `limits` registers, so this is two RTTs, not two
        transactions.
        """
        try:
            allowed = await self._limiter.hit(self._item, subject)
            stats = await self._limiter.get_window_stats(self._item, subject)
        except StorageError:
            logger.warning(
                "ratelimit.storage_unavailable",
                storage_uri=_redact(self.storage_uri),
                subject=subject,
            )
            return None

        reset_after = max(0, math.ceil(stats.reset_time - time.time()))
        if not allowed:
            reset_after = max(1, reset_after)
        return RateLimitDecision(
            allowed=allowed,
            limit=self.per_minute,
            remaining=max(0, stats.remaining),
            reset_after=reset_after,
        )

    async def reset(self) -> None:
        """Drop every counter. Test-support only; never called in production."""
        await self._storage.reset()


def _redact(uri: str) -> str:
    """Strip credentials from a storage URI before it reaches a log line."""
    scheme, sep, rest = uri.partition("://")
    if not sep or "@" not in rest:
        return uri
    return f"{scheme}://***@{rest.rsplit('@', 1)[1]}"


def storage_uri_for(settings: Settings) -> str:
    """Translate app settings into a `limits` storage URI.

    `limits` selects its async backends by an ``async+`` scheme prefix; the
    synchronous ones would block the event loop, so an explicit override that
    forgets the prefix is a configuration error worth failing on rather than
    quietly accepting.
    """
    override = settings.api_rate_limit_storage_uri
    if override:
        if not override.startswith("async+"):
            raise ValueError(
                "api_rate_limit_storage_uri must name an async `limits` backend "
                f"(scheme starting with 'async+'); got {override!r}"
            )
        return override

    url = settings.redis_url
    if not url.startswith(("redis://", "rediss://", "unix://")):
        raise ValueError(
            f"cannot derive a rate-limit storage URI from redis_url={url!r}; "
            "set api_rate_limit_storage_uri explicitly"
        )
    return f"async+{url}"


def build_rate_limiter(settings: Settings) -> RateLimitGuard | None:
    """Return a guard, or ``None`` when rate limiting is switched off."""
    if not settings.api_rate_limit_enabled:
        logger.info("ratelimit.disabled")
        return None
    guard = RateLimitGuard(
        per_minute=settings.api_rate_limit_per_minute,
        storage_uri=storage_uri_for(settings),
    )
    logger.info(
        "ratelimit.enabled",
        per_minute=guard.per_minute,
        storage=_redact(guard.storage_uri),
    )
    return guard


async def enforce_rate_limit(
    request: Request,
    response: Response,
    claims: TokenClaims = Depends(get_current_user),
) -> None:
    """Route dependency: consume quota, annotate the response, or raise 429.

    Declaring ``get_current_user`` as a sub-dependency is what orders this
    after token verification — FastAPI resolves the graph, and its dependency
    cache means the endpoint's own ``Depends(get_current_user)`` reuses the
    same verified claims rather than checking the signature twice.

    Mutating ``response.headers`` here is the documented FastAPI mechanism for
    a dependency to contribute headers to a successful response; on the 429
    path the exception carries them instead, because no response object has
    been produced yet.
    """
    guard: RateLimitGuard | None = getattr(request.app.state, "rate_limiter", None)
    if guard is None:
        return

    decision = await guard.check(claims.sub)
    if decision is None:  # storage down — fail open, unmetered
        return

    if not decision.allowed:
        logger.info(
            "ratelimit.exceeded",
            subject=claims.sub,
            limit=decision.limit,
            path=request.url.path,
        )
        raise RateLimitExceeded(decision)

    response.headers.update(decision.headers())


async def rate_limit_exceeded_handler(
    request: Request,
    exc: Exception,
) -> ProblemResponse:
    """RFC 7807 body for a 429, matching every other error this API emits.

    The signature takes a bare ``Exception`` because that is what Starlette's
    handler protocol is typed as; the registration in ``main.py`` guarantees
    only ``RateLimitExceeded`` arrives here.
    """
    assert isinstance(exc, RateLimitExceeded)
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {
        "type": "https://realty-lead-gen.local/errors/rate_limit_exceeded",
        "title": "rate limit exceeded",
        "status": 429,
        "detail": exc.detail,
        "instance": request_id,
        "limit": exc.decision.limit,
        "remaining": exc.decision.remaining,
        "reset_after_seconds": exc.decision.reset_after,
    }
    # `ProblemResponse` carries `application/problem+json` on the class, so the
    # 429 cannot drift onto a different content type from the other errors.
    return ProblemResponse(
        status_code=429,
        content=body,
        headers=exc.decision.headers(),
    )
