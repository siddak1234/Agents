"""The refuse-to-boot rules in `Settings.model_post_init`.

These are the cheapest security controls in the codebase and the easiest to
regress, because nothing exercises them until a production deploy — at which
point the failure mode is silent (a weak key that still verifies tokens, or
webhooks that still deliver, just unsigned). Testing them here is what makes
them real.

Every case constructs `Settings` with `_env_file=None` so a developer's local
`.env` cannot change the outcome of the suite.
"""

from __future__ import annotations

import pytest

from realty_lead_gen.config import Settings

# 48 printable characters — comfortably over the 32-byte HS256 floor.
STRONG_SECRET = "x" * 48


@pytest.fixture(autouse=True)
def _unset_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the field *defaults* show through.

    The suite-wide `_clear_env` fixture exports a strong `JWT_HS_SECRET` so
    that token-minting tests do not trip PyJWT's key-length warning. Here that
    would mask the very defaults under test, and pydantic-settings reads the
    environment regardless of `_env_file=None`. Module-level autouse fixtures
    run after conftest-level ones, so this undoes it for this file only.
    """
    monkeypatch.delenv("JWT_HS_SECRET", raising=False)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


@pytest.mark.unit
def test_production_rejects_the_default_dev_secret() -> None:
    with pytest.raises(RuntimeError, match="non-default JWT_HS_SECRET"):
        _settings(app_env="production")


@pytest.mark.unit
def test_production_rejects_a_short_hs_secret() -> None:
    """RFC 7518 s3.2 puts the HS256 floor at the hash output size, 256 bits.

    PyJWT only *warns* about a shorter key, so without this check a 16-byte
    secret would ship and keep working — which is the whole problem.
    """
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        _settings(app_env="production", jwt_hs_secret="not-thirty-two-bytes")


@pytest.mark.unit
def test_short_hs_secret_is_measured_in_bytes_not_characters() -> None:
    """A 31-character secret of 2-byte code points is still under the floor.

    Entropy lives in the encoded key material PyJWT actually hashes, so the
    check has to encode before measuring; `len()` on the `str` would pass this
    and hand HMAC a key that is short in exactly the way that matters.
    """
    thirty_one_two_byte_chars = "é" * 31  # 31 chars, 62 bytes -> fine
    assert _settings(app_env="production", jwt_hs_secret=thirty_one_two_byte_chars).is_production

    fifteen_two_byte_chars = "é" * 15  # 15 chars, 30 bytes -> too short
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        _settings(app_env="production", jwt_hs_secret=fifteen_two_byte_chars)


@pytest.mark.unit
def test_jwks_deployments_skip_the_hs_secret_rules_entirely() -> None:
    """With a JWKS URL the HMAC field is never read, so it must not gate boot."""
    settings = _settings(
        app_env="production",
        jwt_jwks_url="https://idp.example/.well-known/jwks.json",
    )
    assert settings.jwt_hs_secret.get_secret_value() == "dev-only-do-not-use"


@pytest.mark.unit
def test_development_keeps_the_short_default() -> None:
    """The guardrails are production-only on purpose: no local setup step."""
    assert _settings().jwt_hs_secret.get_secret_value() == "dev-only-do-not-use"
    assert _settings(app_env="test").is_production is False


@pytest.mark.unit
def test_production_rejects_an_unsigned_outbox_webhook() -> None:
    with pytest.raises(RuntimeError, match="OUTBOX_WEBHOOK_SECRET"):
        _settings(
            app_env="production",
            jwt_hs_secret=STRONG_SECRET,
            outbox_webhook_url="https://snoopy.example/hooks/leads",
        )

    signed = _settings(
        app_env="production",
        jwt_hs_secret=STRONG_SECRET,
        outbox_webhook_url="https://snoopy.example/hooks/leads",
        outbox_webhook_secret="a-real-signing-secret",
    )
    assert signed.outbox_webhook_secret is not None


@pytest.mark.unit
def test_database_url_must_use_the_async_driver() -> None:
    """A sync DSN here would not fail until the first query, inside a worker."""
    with pytest.raises(ValueError, match="\\+asyncpg"):
        _settings(database_url="postgresql://rlg:rlg@localhost:5432/rlg")

    with pytest.raises(ValueError, match="postgresql"):
        _settings(database_url="mysql+aiomysql://rlg:rlg@localhost:3306/rlg")
