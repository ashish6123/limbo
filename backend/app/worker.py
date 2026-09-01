"""Standalone reconciliation worker entrypoint: run_worker_loop is what actually
runs forever, reused by both this process (the Render Background Worker in
production, per docs/deployment.md) and, for local docker-compose dev, by
main.py's embedded scheduler task — see settings.run_embedded_scheduler.

Run:
    python -m app.worker
"""

import asyncio
import logging

from app.config import Settings
from app.demo.service import cleanup_expired
from app.models.base import async_session
from app.scheduler import run_reconciliation_cycle

logger = logging.getLogger("limbo.worker")


async def run_worker_loop(settings: Settings) -> None:
    """Runs the reconciliation cycle and demo-session cleanup forever on
    RECON_INTERVAL_SECONDS. Errors in either are logged and do not kill the
    loop, matching run_reconciliation_cycle's own failure handling."""
    while True:
        try:
            await run_reconciliation_cycle(settings)
        except Exception:
            logger.exception("reconciliation cycle failed")
        try:
            async with async_session() as session:
                await cleanup_expired(session)
        except Exception:
            logger.exception("demo session cleanup failed")
        await asyncio.sleep(settings.recon_interval_seconds)


async def _main() -> None:
    from app.config import settings
    from app.models.base import Base, engine
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_worker_loop(settings)


if __name__ == "__main__":
    asyncio.run(_main())
