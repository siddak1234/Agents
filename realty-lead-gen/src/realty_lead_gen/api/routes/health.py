"""Liveness + readiness endpoints. No auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from realty_lead_gen.api.deps import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=dict[str, str])
async def healthz() -> dict[str, str]:
    """Liveness — the process is running."""
    return {"status": "ok"}


@router.get("/readyz", response_model=dict[str, str])
async def readyz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Readiness — dependencies are reachable."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}") from e
    return {"status": "ready"}
