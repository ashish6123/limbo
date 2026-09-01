import pytest_asyncio

from app.models.base import Base, async_session, engine
import app.models  # noqa: F401


@pytest_asyncio.fixture
async def session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as s:
        yield s
        await s.rollback()

    # pytest-asyncio gives each test function its own event loop by default;
    # asyncpg connections are bound to the loop they were opened on, so the
    # pooled connection must not survive into the next test's loop.
    await engine.dispose()
