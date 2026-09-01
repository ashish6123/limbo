from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.schemas import PaymentEntity, WebhookPayload
from app.models.enums import OrderState, WebhookEventType
from app.models.order import Order
from app.models.webhook_event import WebhookEvent
from app.time_provider import SystemTimeProvider, TimeProvider


class MerchantService:
    """Owns the merchant's own view of order state. Transitions orders only in
    response to webhooks it actually receives — the whole point of Limbo is that
    this view can silently diverge from the gateway's."""

    def __init__(self, session: AsyncSession, time_provider: TimeProvider = SystemTimeProvider()):
        self._session = session
        self._time = time_provider

    async def create_order(self, order_id: str, amount_paise: int, currency: str = "INR") -> Order:
        order = Order(
            id=order_id,
            amount_paise=amount_paise,
            currency=currency,
            state=OrderState.AWAITING_PAYMENT,
            created_at=self._time.now(),
        )
        self._session.add(order)
        await self._session.flush()
        return order

    async def receive_webhook(
        self,
        payload: WebhookPayload,
        delivered_at: datetime | None = None,
        attempt_number: int = 1,
    ) -> None:
        entity = payload.payload.get("payment", {}).get("entity")
        payment_id = entity.id if isinstance(entity, PaymentEntity) else None
        delivered_at = delivered_at or self._time.now()

        self._session.add(
            WebhookEvent(
                payment_id=payment_id,
                event_type=payload.event,
                delivered=True,
                delivered_at=delivered_at,
                merchant_response_code=200,
                attempt_number=attempt_number,
            )
        )

        if payload.event == WebhookEventType.PAYMENT_CAPTURED and isinstance(entity, PaymentEntity):
            order = await self._session.get(Order, entity.order_id)
            if order is not None and order.state == OrderState.AWAITING_PAYMENT:
                order.state = OrderState.CONFIRMED

        await self._session.flush()

    async def cancel_order(self, order_id: str) -> None:
        """Ordinary merchant-initiated cancellation (customer request, stock-out,
        fraud check, etc.) — distinct from expire_unconfirmed_orders' timeout
        sweep. Idempotent: a no-op if already cancelled."""
        order = await self._session.get(Order, order_id)
        if order is None:
            raise ValueError(f"cannot cancel unknown order {order_id!r}")
        if order.state == OrderState.CANCELLED:
            return
        order.state = OrderState.CANCELLED
        await self._session.flush()

    async def force_complete_order(self, order_id: str) -> None:
        """REPLAY_WEBHOOK recovery action: the recon engine has already confirmed
        the gateway payment succeeded, so this overrides the merchant's stale
        CANCELLED state instead of waiting for another webhook that will never
        arrive. Idempotent: a no-op if the order is already CONFIRMED/FULFILLED."""
        order = await self._session.get(Order, order_id)
        if order is None:
            raise ValueError(f"cannot force-complete unknown order {order_id!r}")
        if order.state in (OrderState.CONFIRMED, OrderState.FULFILLED):
            return
        order.state = OrderState.CONFIRMED
        await self._session.flush()

    async def expire_unconfirmed_orders(self, cutoff: datetime, order_ids: list[str] | None = None) -> list[str]:
        """Merchant's own timeout sweep: orders still AWAITING_PAYMENT past the
        fulfillable window are cancelled independent of whether a webhook ever
        arrives. This is what actually produces LIMBO_PAID_CANCELLED — a captured
        payment left orphaned against a cancelled order.

        Pass order_ids to scope the sweep to specific orders — needed by callers
        driving one order under a fast-forwarded cutoff, so that cutoff isn't
        applied to every other AWAITING_PAYMENT order in the DB."""
        query = select(Order).where(Order.state == OrderState.AWAITING_PAYMENT, Order.created_at < cutoff)
        if order_ids is not None:
            query = query.where(Order.id.in_(order_ids))
        orders = (await self._session.execute(query)).scalars().all()

        for order in orders:
            order.state = OrderState.CANCELLED

        await self._session.flush()
        return [order.id for order in orders]
