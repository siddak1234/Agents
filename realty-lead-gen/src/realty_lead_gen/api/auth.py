"""JWT verification.

We verify tokens the frontend (Snoopy) mints. Two modes:

    * Production: JWKS URL — fetch and cache the JWKS document, verify
      RS/ES-signed tokens against it.
    * Dev / test: shared HS secret in `JWT_HS_SECRET`.

We never issue tokens. Sign-in / MFA lives in Snoopy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger

logger = get_logger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: str
    email: str | None
    role: str
    raw: dict[str, Any]


@lru_cache(maxsize=1)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


class JWKSFetcher:
    """Lightweight JWKS fetcher for testability."""

    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = url
        self._client = client or httpx.AsyncClient(timeout=5.0)

    async def get(self) -> dict[str, Any]:
        resp = await self._client.get(self.url)
        resp.raise_for_status()
        # `httpx.Response.json()` is `Any`; bind it to a typed local so the
        # `Any` stops at this boundary instead of leaking out of the return.
        document: dict[str, Any] = resp.json()
        return document


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> TokenClaims:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    token = credentials.credentials
    try:
        if settings.jwt_jwks_url is not None:
            client = _jwks_client(str(settings.jwt_jwks_url))
            signing_key = client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
            )
        else:
            claims = jwt.decode(
                token,
                settings.jwt_hs_secret.get_secret_value(),
                algorithms=["HS256"],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
            )
    except jwt.PyJWTError as e:
        logger.info("auth.token_invalid", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
        ) from e

    # Reject if `exp` explicit but in the past — PyJWT already checks, but belt+braces.
    exp = claims.get("exp")
    if exp is not None and datetime.fromtimestamp(exp, tz=UTC) < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token expired",
        )

    return TokenClaims(
        sub=claims["sub"],
        email=claims.get("email"),
        role=claims.get("role", "realtor"),
        raw=claims,
    )
