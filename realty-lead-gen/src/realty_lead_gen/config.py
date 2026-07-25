"""Application configuration.

Loaded once at startup and validated by pydantic-settings. Every field is
typed; missing required fields fail fast rather than crashing at request
time. Adapters check their own credentials and self-disable when absent —
so a partial config is a first-class supported state (dev, demo, staging
without vendor keys).

Design notes:
    * DATABASE_URL uses the async driver form (`postgresql+asyncpg://...`).
      Alembic uses the same URL and the same driver — see `alembic/env.py`.
    * JWT verification supports both a JWKS URL (production) and an HS
      symmetric secret (dev). We refuse to boot in production with the
      dev secret still set — see `Settings.model_post_init`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]

#: Minimum HS256 signing-key length. RFC 7518 s3.2 requires a key at least as
#: long as the hash output (SHA-256 -> 256 bits -> 32 bytes). PyJWT only warns
#: below this, so the floor is enforced here instead — see `model_post_init`.
MIN_HS256_SECRET_BYTES = 32


class Settings(BaseSettings):
    """Typed environment settings.

    Mutations are not supported — treat this as immutable after construction.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime -------------------------------------------------------------
    app_env: Environment = "development"
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    app_log_format: Literal["json", "console"] = "json"

    # --- HTTP ----------------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 — intentional container bind
    api_port: int = 8000
    api_public_url: HttpUrl = Field(default=HttpUrl("http://localhost:8000"))
    cors_origins: str = "http://localhost:3000"

    # --- Database ------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://rlg:rlg@localhost:5432/rlg"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # --- Redis ---------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth ----------------------------------------------------------------
    jwt_jwks_url: HttpUrl | None = None
    jwt_hs_secret: SecretStr = SecretStr("dev-only-do-not-use")
    jwt_issuer: str = "snoopy"
    jwt_audience: str = "realty-lead-gen"

    # --- LLM -----------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    anthropic_model_reasoning: str = "claude-sonnet-4-5"
    anthropic_model_vision: str = "claude-sonnet-4-5"
    anthropic_model_cheap: str = "claude-haiku-4-5"
    llm_max_cost_per_lead_usd: float = 1.00

    # --- Source credentials — all optional -----------------------------------
    reso_trestle_token: SecretStr | None = None
    reso_trestle_base_url: HttpUrl = Field(default=HttpUrl("https://api-trestle.corelogic.com"))
    mls_grid_token: SecretStr | None = None
    bright_data_zone: str | None = None
    bright_data_token: SecretStr | None = None
    rapidapi_key: SecretStr | None = None
    rapidapi_zillow_host: str = "zillow-com1.p.rapidapi.com"
    propertyradar_api_token: SecretStr | None = None
    attom_api_key: SecretStr | None = None
    batchskiptracing_api_key: SecretStr | None = None
    rentcast_api_key: SecretStr | None = None
    housecanary_api_key: SecretStr | None = None

    # --- Outbox relay --------------------------------------------------------
    # Unset means "no sink": the relay drains events to the log and marks
    # them dispatched, so a frontend-less deployment cannot grow the table
    # without bound. Set both to push lead.surfaced events to Snoopy.
    outbox_webhook_url: HttpUrl | None = None
    outbox_webhook_secret: SecretStr | None = None
    outbox_webhook_timeout_seconds: float = 10.0

    # --- Budgets -------------------------------------------------------------
    ingest_max_listings_per_run: int = 500
    enrich_max_concurrency: int = 8
    api_rate_limit_per_minute: int = 120
    api_rate_limit_enabled: bool = True
    # Where the rate limiter keeps its counters. Unset means "derive it from
    # `redis_url`", which is what any real deployment wants: the quota has to
    # be shared across API workers or each worker enforces its own copy of it.
    # Override only to point at a different Redis, or to `async+memory://` for
    # a single-process dev run. Must name an async `limits` backend — see
    # api/ratelimit.py.
    api_rate_limit_storage_uri: str | None = None

    # --- Derived -------------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if not parsed.scheme.startswith("postgresql"):
            raise ValueError(f"database_url must be postgresql://; got {parsed.scheme}")
        if "+asyncpg" not in parsed.scheme:
            raise ValueError("database_url must use the +asyncpg driver form")
        return v

    def model_post_init(self, __context: object) -> None:
        # Guardrail — refuse to boot in production with a dev JWT secret.
        if (
            self.is_production
            and self.jwt_jwks_url is None
            and self.jwt_hs_secret.get_secret_value() == "dev-only-do-not-use"
        ):
            raise RuntimeError(
                "Refusing to start: production requires JWT_JWKS_URL "
                "or a non-default JWT_HS_SECRET."
            )
        # Guardrail — HS256 keys shorter than the hash output are weak, and
        # PyJWT warns rather than refusing, so a short secret would otherwise
        # ship silently. Only enforced when HMAC is the verification path in
        # production; JWKS deployments never read this field, and dev keeps
        # the short default so nothing local has to change. Measured on the
        # encoded bytes, not the character count — that is what gets hashed.
        if (
            self.is_production
            and self.jwt_jwks_url is None
            and len(self.jwt_hs_secret.get_secret_value().encode()) < MIN_HS256_SECRET_BYTES
        ):
            raise RuntimeError(
                f"Refusing to start: JWT_HS_SECRET must be at least "
                f"{MIN_HS256_SECRET_BYTES} bytes for HS256 (RFC 7518 s3.2). "
                "Generate one with `openssl rand -base64 48`, or set "
                "JWT_JWKS_URL instead."
            )
        # Guardrail — an unsigned production webhook lets anyone who learns
        # the URL forge lead events into the frontend.
        if (
            self.is_production
            and self.outbox_webhook_url is not None
            and self.outbox_webhook_secret is None
        ):
            raise RuntimeError(
                "Refusing to start: OUTBOX_WEBHOOK_URL is set in production "
                "without OUTBOX_WEBHOOK_SECRET, so deliveries would be unsigned."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Call once per process lifetime."""
    return Settings()
