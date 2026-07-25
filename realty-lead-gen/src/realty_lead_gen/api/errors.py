"""RFC 7807 Problem Details error responses.

Every non-2xx from the API returns a Problem body:

    {
        "type":    "https://api.example/errors/<slug>",
        "title":   "<human-readable>",
        "status":  <int>,
        "detail":  "<message>",
        "instance": "<request-id>",
        "errors":   [...]     # optional, for validation failures
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import orjson
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from realty_lead_gen.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    # Safe to defer, unlike on a route signature: Starlette invokes exception
    # handlers positionally (`await handler(conn, exc)` in
    # `_exception_handler.wrap_app_handling_exceptions`) and never resolves
    # their annotations, so nothing needs `Request` at runtime.
    from fastapi import Request

logger = get_logger(__name__)


class ProblemResponse(Response):
    """A response whose body is an RFC 7807 problem document.

    Carrying the media type on the class rather than passing it at each call
    site is what makes it impossible to emit a problem body under the wrong
    content type — a client that content-negotiates on
    ``application/problem+json`` would otherwise silently miss some errors.

    Rendering goes through orjson directly rather than FastAPI's
    ``ORJSONResponse``, which is deprecated as of FastAPI 0.139 (it now
    serializes through Pydantic for *declared* response models; an exception
    handler has no declared model, so the substitution FastAPI suggests does
    not apply and the class is simply going away under us).
    """

    media_type = "application/problem+json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)


def _problem(
    *,
    status: int,
    title: str,
    slug: str,
    detail: str | None,
    request_id: str | None,
    extras: dict[str, Any] | None = None,
    # `Mapping`, not `dict`: Starlette types `HTTPException.headers` that way
    # and narrowing here would reject the exception's own headers.
    headers: Mapping[str, str] | None = None,
) -> ProblemResponse:
    body: dict[str, Any] = {
        "type": f"https://realty-lead-gen.local/errors/{slug}",
        "title": title,
        "status": status,
        "detail": detail or title,
        "instance": request_id,
    }
    if extras:
        body.update(extras)
    return ProblemResponse(status_code=status, content=body, headers=headers)


async def http_exception_handler(request: Request, exc: Exception) -> ProblemResponse:
    assert isinstance(exc, StarletteHTTPException)
    request_id = getattr(request.state, "request_id", None)
    slug = "http_error" if not exc.detail else exc.detail.__class__.__name__.lower()
    return _problem(
        status=exc.status_code,
        title=str(exc.detail) or "HTTP error",
        slug=slug,
        detail=str(exc.detail) or None,
        request_id=request_id,
        # A 401 without its WWW-Authenticate challenge, or a 405 without Allow,
        # is a protocol violation; Starlette attaches those to the exception and
        # they would be dropped if we only copied the body.
        headers=exc.headers,
    )


async def validation_exception_handler(
    request: Request,
    exc: Exception,
) -> ProblemResponse:
    assert isinstance(exc, RequestValidationError)
    request_id = getattr(request.state, "request_id", None)
    return _problem(
        status=422,
        title="validation failed",
        slug="validation",
        detail="One or more request fields failed validation.",
        request_id=request_id,
        # `errors()` can carry a live exception instance under `ctx` (Pydantic
        # v2 puts the original ValueError there), which orjson refuses to
        # serialize. `jsonable_encoder` reduces those to strings, so a bad
        # request body cannot turn a 422 into a 500.
        extras={"errors": jsonable_encoder(exc.errors())},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> ProblemResponse:
    request_id = getattr(request.state, "request_id", None)
    # LOG004 flags `.exception()` outside a syntactic `except` block, but this
    # *is* one semantically: Starlette calls exception handlers from inside its
    # own `except Exception as exc:`, so `sys.exc_info()` is still populated
    # and the traceback is captured. Downgrading to `.error()` would silently
    # drop the stack trace from every 500 we log.
    logger.exception(  # noqa: LOG004
        "api.unhandled_exception",
        request_id=request_id,
        path=request.url.path,
        method=request.method,
    )
    return _problem(
        status=500,
        title="internal server error",
        slug="internal",
        detail="Unexpected error. Reference this request_id when reporting.",
        request_id=request_id,
    )
