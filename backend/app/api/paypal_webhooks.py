"""PayPal webhook receiver (POST /webhooks/paypal).

Every event is verified with PayPal's verify-webhook-signature API before it is
trusted, de-duplicated on PayPal's event id, and mapped onto Limbo's existing
ledger and webhook records, so reconciliation, diagnosis and policy code stay
gateway-agnostic. A dispute opened on a payment Limbo knows about is escalated
to a human at once, with a Gemini-drafted response built from the audit trail."""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.config import settings
from app.diagnosis.dispute import draft_dispute_response
from app.diagnosis.llm import make_client
from app.gateway.paypal_adapter import PayPalAdapter, to_minor_units
from app.gateway.schemas import PaymentEntity, WebhookPayload
from app.merchant.service import MerchantService
from app.models.audit_log import AuditLog
from app.models.base import async_session
from app.models.enums import PaymentGatewayStatus, WebhookEventType
from app.models.order import Order
from app.models.payment import Payment
from app.models.policy_action import PolicyAction
from app.policy.enums import ActionStatus, ActionType

router = APIRouter(prefix="/webhooks")

HANDLED_EVENTS = (
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
    "CUSTOMER.DISPUTE.CREATED",
)


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@router.post("/paypal")
async def paypal_webhook(request: Request) -> dict:
    if settings.gateway_mode != "paypal" or not settings.paypal_webhook_id:
        raise HTTPException(status_code=404, detail="PayPal webhooks are not enabled on this deployment")

    event = await request.json()
    event_id = event.get("id")
    event_type = event.get("event_type")
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="not a PayPal webhook event")

    async def _no_delivery(payload):
        pass

    async with httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=30.0) as http, async_session() as session:
        adapter = PayPalAdapter(
            session, http, settings.paypal_client_id, settings.paypal_client_secret, _no_delivery,
            currency=settings.paypal_currency,
        )
        if not await adapter.verify_webhook_signature(dict(request.headers), event, settings.paypal_webhook_id):
            raise HTTPException(status_code=401, detail="PayPal webhook signature verification failed")

        # PayPal retries until it gets a 2xx; the event id makes redelivery a no-op.
        seen = (
            await session.execute(select(AuditLog.id).where(AuditLog.idempotency_key == f"paypal:{event_id}"))
        ).first()
        if seen is not None:
            return {"status": "duplicate", "event_id": event_id}

        if event_type not in HANDLED_EVENTS:
            return {"status": "ignored", "event_type": event_type}

        resource = event.get("resource", {})
        if event_type == "PAYMENT.CAPTURE.COMPLETED":
            outcome = await _capture_completed(session, resource)
        elif event_type in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
            outcome = await _capture_refunded(session, adapter, resource)
        else:
            outcome = await _dispute_created(session, resource)

        await audit.record(
            session,
            stage="webhook",
            event=event_type.lower(),
            order_id=outcome.get("order_id"),
            payment_id=outcome.get("payment_id"),
            idempotency_key=f"paypal:{event_id}",
            inputs={"paypal_event_id": event_id, **{k: v for k, v in outcome.items() if k != "reasoning"}},
            reasoning=outcome.get("reasoning"),
        )
        await session.commit()
        return {"status": "processed", "event_type": event_type}


async def _capture_completed(session: AsyncSession, capture: dict) -> dict:
    capture_id = capture["id"]
    order_id = capture.get("custom_id")
    order = await session.get(Order, order_id) if order_id else None
    if order is None:
        return {"payment_id": capture_id, "reasoning": "capture has no custom_id matching a Limbo order; recorded only"}

    payment = await session.get(Payment, capture_id)
    if payment is None:
        payment = Payment(
            id=capture_id,
            order_id=order_id,
            amount_paise=to_minor_units(capture["amount"]["value"]),
            gateway_status=PaymentGatewayStatus.CAPTURED,
            method="paypal",
            captured_at=_parse_time(capture.get("create_time")),
        )
        session.add(payment)
        await session.flush()

    entity = PaymentEntity(
        id=capture_id,
        order_id=order_id,
        amount=payment.amount_paise,
        currency=capture["amount"]["currency_code"],
        status=PaymentGatewayStatus.CAPTURED.value,
        method="paypal",
    )
    await MerchantService(session).receive_webhook(
        WebhookPayload(event=WebhookEventType.PAYMENT_CAPTURED, payload={"payment": {"entity": entity}})
    )
    return {"order_id": order_id, "payment_id": capture_id, "reasoning": "PayPal confirmed the capture; order confirmed."}


async def _capture_refunded(session: AsyncSession, adapter: PayPalAdapter, refund: dict) -> dict:
    # The resource is the refund; its "up" link points at the capture it belongs to.
    capture_id = next(
        (link["href"].rstrip("/").rsplit("/", 1)[-1] for link in refund.get("links", []) if link.get("rel") == "up"),
        None,
    )
    payment = await session.get(Payment, capture_id) if capture_id else None
    if payment is None:
        return {"payment_id": capture_id, "reasoning": "refund for a capture Limbo does not track; recorded only"}
    capture = await adapter.fetch_existing_capture(capture_id)
    if capture.get("status") == "REFUNDED":
        payment.gateway_status = PaymentGatewayStatus.REFUNDED
        await session.flush()
    return {
        "order_id": payment.order_id,
        "payment_id": capture_id,
        "refund_id": refund.get("id"),
        "reasoning": f"PayPal reports refund {refund.get('id')} as {refund.get('status')}; capture is now {capture.get('status')}.",
    }


async def _dispute_created(session: AsyncSession, dispute: dict) -> dict:
    dispute_id = dispute.get("dispute_id")
    capture_ids = [t.get("seller_transaction_id") for t in dispute.get("disputed_transactions", [])]
    payment = None
    for capture_id in filter(None, capture_ids):
        payment = await session.get(Payment, capture_id)
        if payment is not None:
            break
    if payment is None:
        return {"dispute_id": dispute_id, "reasoning": "dispute on a transaction Limbo does not track; recorded only"}

    history = await audit.for_order(session, payment.order_id)
    draft = None
    if settings.gemini_api_key:
        draft = await draft_dispute_response(
            make_client(settings.gemini_api_key), dispute, history
        )
    reasoning = (
        f"Buyer opened PayPal dispute {dispute_id} ({dispute.get('reason')}) on a payment Limbo was tracking. "
        "Escalated to a human: disputes are never answered automatically."
    )
    if draft:
        reasoning += f"\n\nGemini-drafted response for review:\n{draft}"

    # An ESCALATED action puts the case in the dashboard's exception queue.
    session.add(
        PolicyAction(
            order_id=payment.order_id,
            payment_id=payment.id,
            action_type=ActionType.ESCALATE_HUMAN,
            idempotency_key=f"{payment.order_id}:dispute:{dispute_id}",
            attempt_number=1,
            status=ActionStatus.ESCALATED,
            amount_paise=payment.amount_paise,
            reasoning=reasoning,
        )
    )
    await session.flush()
    return {"order_id": payment.order_id, "payment_id": payment.id, "dispute_id": dispute_id, "reasoning": reasoning}
