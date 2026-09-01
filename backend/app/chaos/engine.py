import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.chaos.enums import FailureClass
from app.gateway.schemas import PaymentEntity, WebhookPayload
from app.merchant.service import MerchantService
from app.models.chaos_injection import ChaosInjection
from app.models.enums import RefundGatewayStatus
from app.models.refund import Refund
from app.models.webhook_event import WebhookEvent
from app.time_provider import SystemTimeProvider, TimeProvider


class ChaosEngine:
    """Sits between the gateway and the merchant's webhook receiver. Each of the
    six injectable classes produces a distinct, verifiable DB footprint rather
    than a special-cased short-circuit, so reconciliation and diagnosis have real
    evidence to work from later."""

    def __init__(self, session: AsyncSession, time_provider: TimeProvider = SystemTimeProvider()):
        self._session = session
        self._time = time_provider

    async def deliver(
        self,
        payload: WebhookPayload,
        merchant: MerchantService,
        injected: FailureClass | None,
    ) -> None:
        entity = payload.payload.get("payment", {}).get("entity")
        payment_id = entity.id if isinstance(entity, PaymentEntity) else None
        order_id = entity.order_id if isinstance(entity, PaymentEntity) else None

        if injected is not None:
            self._session.add(
                ChaosInjection(
                    order_id=order_id,
                    payment_id=payment_id,
                    failure_class=injected.value,
                    injected_at=self._time.now(),
                )
            )

        if injected == FailureClass.WEBHOOK_DROPPED:
            return  # never reaches the merchant: no webhook_events row, no state change

        if injected == FailureClass.WEBHOOK_MERCHANT_500:
            self._session.add(
                WebhookEvent(
                    payment_id=payment_id,
                    event_type=payload.event,
                    delivered=False,
                    delivered_at=self._time.now(),
                    merchant_response_code=500,
                    attempt_number=1,
                )
            )
            await self._session.flush()
            return  # merchant's handler crashed before committing the order transition

        if injected == FailureClass.WEBHOOK_OUT_OF_ORDER:
            t0 = self._time.now()
            await merchant.receive_webhook(payload, delivered_at=t0, attempt_number=2)
            await merchant.receive_webhook(payload, delivered_at=t0 + timedelta(seconds=1), attempt_number=1)
            return  # the retry (attempt 2) arrived before the original (attempt 1)

        if injected == FailureClass.WEBHOOK_DUPLICATED:
            t0 = self._time.now()
            await merchant.receive_webhook(payload, delivered_at=t0, attempt_number=1)
            await merchant.receive_webhook(payload, delivered_at=t0 + timedelta(seconds=1), attempt_number=2)
            return  # the exact same event was delivered twice

        # WEBHOOK_DELAYED, REFUND_STUCK (delivered normally at capture time), and healthy
        # all deliver the webhook now; a caller simulating WEBHOOK_DELAYED is expected to
        # withhold this call and invoke it later, after fast-forwarding the clock.
        await merchant.receive_webhook(payload, delivered_at=self._time.now(), attempt_number=1)

    async def initiate_refund_stuck(self, payment_id: str, amount_paise: int) -> str:
        """REFUND_STUCK: the gateway marks the refund initiated but its settlement
        callback never arrives — gateway_status stays 'initiated', settled_at stays
        null past the SLA window."""
        refund_id = f"rfnd_{uuid.uuid4().hex[:14]}"
        now = self._time.now()
        self._session.add(
            Refund(
                id=refund_id,
                payment_id=payment_id,
                amount_paise=amount_paise,
                gateway_status=RefundGatewayStatus.INITIATED,
                initiated_at=now,
                settled_at=None,
            )
        )
        self._session.add(
            ChaosInjection(
                payment_id=payment_id,
                failure_class=FailureClass.REFUND_STUCK.value,
                injected_at=now,
            )
        )
        await self._session.flush()
        return refund_id
