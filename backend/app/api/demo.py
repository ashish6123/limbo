import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from app.audit import writer as audit
from app.config import settings
from app.demo import service as demo_service
from app.demo.rate_limit import enforce_rate_limit
from app.models.base import async_session

router = APIRouter(prefix="/api/demo", dependencies=[Depends(enforce_rate_limit)])

_SSE_MAX_IDLE_TICKS = 60


class CreateOrderRequest(BaseModel):
    scenario: Literal["replay", "refund"]


class DemoOrderResponse(BaseModel):
    order_id: str
    token: str
    scenario: str
    amount_paise: int
    checkout_url: str | None


class TokenBody(BaseModel):
    token: str


class WebhookDropResponse(BaseModel):
    order_id: str
    status: str


@router.post("/orders", response_model=DemoOrderResponse)
async def create_demo_order(body: CreateOrderRequest) -> DemoOrderResponse:
    async with async_session() as session:
        demo_session, checkout_url = await demo_service.create_order(session, settings, body.scenario)
        return DemoOrderResponse(
            order_id=demo_session.order_id,
            token=demo_session.token,
            scenario=demo_session.scenario,
            amount_paise=settings.demo_order_amount_paise,
            checkout_url=checkout_url,
        )


@router.post("/webhook-drop/{order_id}", response_model=WebhookDropResponse)
async def drop_webhook(order_id: str, body: TokenBody) -> WebhookDropResponse:
    async with async_session() as session:
        try:
            demo_session = await demo_service.get_session(session, order_id, body.token)
            payment = await demo_service.drop_webhook(session, settings, demo_session)
            await demo_service.drive_scenario(session, settings, demo_session, payment)
        except demo_service.DemoAuthorizationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except demo_service.DemoStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WebhookDropResponse(order_id=order_id, status=demo_session.status)


@router.post("/run-failure-scenario", response_model=DemoOrderResponse)
async def run_failure_scenario(body: CreateOrderRequest) -> DemoOrderResponse:
    async with async_session() as session:
        demo_session, _ = await demo_service.create_order(session, settings, body.scenario, force_mock=True)
        payment = await demo_service.drop_webhook(session, settings, demo_session)
        await demo_service.drive_scenario(session, settings, demo_session, payment)
        return DemoOrderResponse(
            order_id=demo_session.order_id,
            token=demo_session.token,
            scenario=demo_session.scenario,
            amount_paise=settings.demo_order_amount_paise,
            checkout_url=None,
        )


@router.get("/{order_id}/stream")
async def stream_events(order_id: str, token: str) -> StreamingResponse:
    """SSE stream of this order's real audit_log rows, oldest first, polling the
    DB directly — no parallel event system. Every emitted event corresponds to a
    persisted row; nothing is fabricated or optimistically shown here or by the
    frontend consuming this stream."""
    async with async_session() as auth_session:
        try:
            await demo_service.get_session(auth_session, order_id, token)
        except demo_service.DemoAuthorizationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def event_source():
        last_id = 0
        idle_ticks = 0
        while idle_ticks < _SSE_MAX_IDLE_TICKS:
            async with async_session() as session:
                rows = await audit.for_order(session, order_id, after_id=last_id)
            if rows:
                idle_ticks = 0
                for row in rows:
                    last_id = row.id
                    payload = {
                        "id": row.id,
                        "stage": row.stage,
                        "event": row.event,
                        "reasoning": row.reasoning,
                        "created_at": row.created_at.isoformat(),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
            else:
                idle_ticks += 1
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_source(), media_type="text/event-stream")
