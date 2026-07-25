"""Structured logging setup.

Uses structlog with JSON output in production and pretty console output
in development. Bound context (request_id, user_id, property_id, ...)
survives across `await` boundaries via structlog's contextvars.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from realty_lead_gen.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure the root logger and structlog processors.

    Idempotent — safe to call multiple times.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.app_log_level,
    )
    # Silence noisy libraries.
    for noisy in ("httpx", "httpcore", "uvicorn.access", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.app_log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.app_log_level)
        ),
        context_class=dict,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger with the given name."""
    # `structlog.get_logger` is annotated `-> Any` because the concrete
    # wrapper class is a configuration choice, not a static one. Ours is
    # fixed in `configure_logging` above (`make_filtering_bound_logger`
    # over the stdlib logger), so the cast asserts what that configuration
    # already guarantees — and returning `Any` here would erase the types
    # on every `logger.info(...)` call in the codebase.
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
