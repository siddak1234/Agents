"""Agent configuration.

Loaded once and validated by pydantic-settings. Every field is typed, and a
missing credential is a first-class supported state: the photo grader checks
its own key and returns `unavailable` rather than crashing, so a partial
config (dev, demo, CI) always works.

This used to be the full service's Settings — database pools, JWT
verification, a dozen vendor keys, an outbox relay. Those fields and their
boot-time guardrails moved to the realty-lead-gen-service repository with
the code that reads them. What remains is exactly what this agent's
capabilities read, which is the point: an agent's config is part of its
tool surface, and fields nothing reads are claims nothing checks.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


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

    # --- LLM -----------------------------------------------------------------
    # The key is optional on purpose: absent means grading is disabled, never
    # broken. See agents/photo_grader.py.
    anthropic_api_key: SecretStr | None = None
    # ANTHROPIC_MODEL_VISION is configurable — but claude_client.py's
    # messages_create sends temperature=0.0 unconditionally, which the
    # current default (claude-sonnet-4-5) accepts and claude-sonnet-5,
    # Opus 4.6+, and Fable 5 reject outright (400 on every call). See the
    # comment on that parameter before pointing this at a newer model.
    anthropic_model_vision: str = "claude-sonnet-4-5"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Call once per process lifetime."""
    return Settings()
