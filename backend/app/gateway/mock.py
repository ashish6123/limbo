import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.base import GatewayAdapter, WebhookSink
from app.gateway.schemas import PaymentEntity, RefundEntity, WebhookPayload
from app.models.enums import PaymentGatewayStatus, RefundGatewayStatus, WebhookEventType
from app.models.payment import Payment
from app.models.refund import Refund
from app.time_provider import SystemTimeProvider, TimeProvider


class MockGatewayAdapter(GatewayAdapter):
    """Simulates Razorpay: writes to the payments/refunds ledger and emits webhooks
    shaped exactly like Razorpay's real payload, so the merchant receiver needs no
    changes when GATEWAY_MODE switches to razorpay."""

    def __init__(
        self,
        session: AsyncSession,
        webhook_sink: WebhookSink,
        time_provider: TimeProvider = SystemTimeProvider(),
    ):
        self._session = session
        self._webhook_sink = webhook_sink
        self._time = time_provider

    async def capture_payment(self, order_id: str, amount_paise: int, method: str) -> str:
        payment_id = f"pay_{uuid.uuid4().hex[:14]}"
        now = self._time.now()
        payment = Payment(
            id=payment_id,
            order_id=order_id,
            amount_paise=amount_paise,
            gateway_status=PaymentGatewayStatus.CAPTURED,
            method=method,
            captured_at=now,
        )
        self._session.add(payment)
        await self._session.flush()

        await self._webhook_sink(
            WebhookPayload(
                event=WebhookEventType.PAYMENT_CAPTURED,
                payload={
                    "payment": {
                        "entity": PaymentEntity(
                            id=payment_id,
                            order_id=order_id,
                            amount=amount_paise,
                            currency="INR",
                            status=PaymentGatewayStatus.CAPTURED.value,
                            method=method,
                        )
                    }
                },
            )
        )
        return payment_id

    async def create_refund(self, payment_id: str, amount_paise: int) -> str:
        refund_id = f"rfnd_{uuid.uuid4().hex[:14]}"
        now = self._time.now()
        refund = Refund(
            id=refund_id,
            payment_id=payment_id,
            amount_paise=amount_paise,
            gateway_status=RefundGatewayStatus.PROCESSED,
            initiated_at=now,
            settled_at=now,
        )
        self._session.add(refund)

        payment = await self._session.get(Payment, payment_id)
        if payment is not None:
            payment.gateway_status = PaymentGatewayStatus.REFUNDED

        await self._session.flush()

        await self._webhook_sink(
            WebhookPayload(
                event=WebhookEventType.REFUND_PROCESSED,
                payload={
                    "refund": {
                        "entity": RefundEntity(
                            id=refund_id,
                            payment_id=payment_id,
                            amount=amount_paise,
                            status=RefundGatewayStatus.PROCESSED.value,
                        )
                    }
                },
            )
        )
        return refund_id

    async def retry_refund_settlement(self, refund_id: str) -> None:
        refund = await self._session.get(Refund, refund_id)
        if refund is None:
            raise ValueError(f"cannot retry settlement for unknown refund {refund_id!r}")
        if refund.gateway_status == RefundGatewayStatus.PROCESSED:
            return
        refund.gateway_status = RefundGatewayStatus.PROCESSED
        refund.settled_at = self._time.now()

        payment = await self._session.get(Payment, refund.payment_id)
        if payment is not None:
            payment.gateway_status = PaymentGatewayStatus.REFUNDED

        await self._session.flush()
