"""Request ID + structured logging middleware.

Rate limiting is deliberately *not* here: it keys on the authenticated
subject, which only exists after dependency resolution, so it lives as a
route dependency instead (see api/ratelimit.py).
"""

from __future__ import annotations

import uuid
from time import monotonic
from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from realty_lead_gen.logging import get_logger

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("api.request.failed")
            raise
        else:
            duration_ms = (monotonic() - start) * 1000
            logger.info(
                "api.request",
                status=response.status_code,
                duration_ms=round(duration_ms, 1),
            )
            response.headers["x-request-id"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()
