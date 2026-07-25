"""FastAPI application factory + entry point.

Composed so tests can build the app against a test settings object
without importing the process-wide singleton.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from realty_lead_gen import __version__
from realty_lead_gen.api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from realty_lead_gen.api.middleware import RequestContextMiddleware
from realty_lead_gen.api.ratelimit import (
    RateLimitExceeded,
    build_rate_limiter,
    enforce_rate_limit,
    rate_limit_exceeded_handler,
)
from realty_lead_gen.api.routes import buyers, health, leads, matches, searches
from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.db import dispose_engine, get_engine
from realty_lead_gen.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings)
    get_engine(settings)  # warm the pool
    logger.info("app.startup", version=__version__, env=settings.app_env)
    yield
    await dispose_engine()
    logger.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="realty-lead-gen",
        version=__version__,
        description="Agentic real-estate lead generation backend.",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.settings = settings
    # `None` when disabled by config; the dependency reads it off app.state so
    # a test can swap the guard out without rebuilding the whole app.
    app.state.rate_limiter = build_rate_limiter(settings)

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Order does not matter to Starlette — it resolves a handler by walking
    # the exception's MRO and taking the most specific registration — but
    # RateLimitExceeded must be registered explicitly, or it would be served
    # by the generic HTTPException handler and lose its Retry-After header.
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Health checks are deliberately outside the quota: a load balancer probing
    # /readyz every second must never consume a human's budget, and the
    # endpoints carry no token to key on anyway.
    app.include_router(health.router)

    metered = [Depends(enforce_rate_limit)]
    app.include_router(leads.router, dependencies=metered)
    app.include_router(searches.router, dependencies=metered)
    app.include_router(buyers.router, dependencies=metered)
    app.include_router(matches.router, dependencies=metered)

    return app


app = create_app()


def run() -> None:
    """Console entry point used by `pyproject.toml`."""
    settings = get_settings()
    uvicorn.run(
        "realty_lead_gen.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.app_env == "development",
    )
