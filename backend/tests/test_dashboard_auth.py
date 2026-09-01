import pytest
from fastapi import HTTPException

from app.api.deps import require_dashboard_key
from app.config import settings


@pytest.mark.asyncio
async def test_open_when_no_key_configured():
    original = settings.dashboard_api_key
    settings.dashboard_api_key = ""
    try:
        await require_dashboard_key(x_api_key=None)
    finally:
        settings.dashboard_api_key = original


@pytest.mark.asyncio
async def test_rejects_missing_or_wrong_key_when_configured():
    original = settings.dashboard_api_key
    settings.dashboard_api_key = "secret123"
    try:
        with pytest.raises(HTTPException) as exc_info:
            await require_dashboard_key(x_api_key=None)
        assert exc_info.value.status_code == 401

        with pytest.raises(HTTPException):
            await require_dashboard_key(x_api_key="wrong")

        await require_dashboard_key(x_api_key="secret123")
    finally:
        settings.dashboard_api_key = original
