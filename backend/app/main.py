import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.demo import router as demo_router
from app.api.routes import router as api_router
from app.config import settings
from app.models.base import Base, engine
from app.worker import run_worker_loop
import app.models  # noqa: F401  ensures all models are registered on Base.metadata


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # In production this API process and the reconciliation worker run as
    # separate services (a Render web service and a Render background worker)
    # so they must not both run the loop — see docs/deployment.md. Local
    # docker-compose dev keeps the embedded task, unchanged, via the default.
    scheduler_task = asyncio.create_task(run_worker_loop(settings)) if settings.run_embedded_scheduler else None
    yield
    if scheduler_task is not None:
        scheduler_task.cancel()


app = FastAPI(title="Limbo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(demo_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
