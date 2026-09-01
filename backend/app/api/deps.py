from typing import AsyncIterator

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.base import async_session


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


async def require_dashboard_key(x_api_key: str | None = Header(default=None)) -> None:
    """Gates the read-only dashboard endpoints behind a shared secret once one
    is configured (DASHBOARD_API_KEY unset keeps local dev open, unchanged).
    This is a light gate against casual/automated access to a publicly
    deployed URL, not real authentication — the dashboard ships the key to the
    browser via NEXT_PUBLIC_DASHBOARD_API_KEY, so it's visible to anyone who
    inspects the deployed frontend's network requests."""
    if not settings.dashboard_api_key:
        return
    if x_api_key != settings.dashboard_api_key:
        raise HTTPException(status_code=401, detail="missing or invalid API key")
