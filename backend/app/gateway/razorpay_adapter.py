from datetime import datetime, timezone

import razorpay
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.base import GatewayAdapter, WebhookSink
from app.gateway.schemas import PaymentEntity, RefundEntity, WebhookPayload
from app.models.enums import PaymentGatewayStatus, RefundGatewayStatus, WebhookEventType
from app.models.payment import Payment
from app.models.refund import Refund


class RazorpayAdapter(GatewayAdapter):
    """Real Razorpay test-mode adapter. Order creation, refunds, payment lookup,
    and webhook signature verification all go through the real API.

    capture_payment has no real equivalent here: Razorpay has no supported,
    non-deprecated way to create a captured payment purely server-side (the S2S
    UPI Collect flow that used to allow this was deprecated 2026-02-28 for all
    but a few exempted use cases Limbo doesn't qualify for). A real payment can
    only be created by a human completing actual Razorpay Checkout. Use
    fetch_existing_payment to bring an already-paid, externally-created payment
    into Limbo's ledger instead — see scripts/razorpay_manual_checkout.py and
    docs/decisions.md ADR-018."""

    def __init__(self, session: AsyncSession, client: razorpay.Client, webhook_sink: WebhookSink):
        self._session = session
        self._client = client
        self._webhook_sink = webhook_sink

    async def capture_payment(self, order_id: str, amount_paise: int, method: str) -> str:
        raise NotImplementedError(
            "RazorpayAdapter cannot create a payment server-side (S2S UPI Collect "
            "is deprecated). Complete a real checkout and call fetch_existing_payment instead."
        )

    async def fetch_existing_payment(self, payment_id: str) -> dict:
        """Brings a real, externally-created payment (from a manual checkout)
        into view. Does not write to our ledger — the caller decides how to
        record it."""
        return self._client.payment.fetch(payment_id)

    async def create_refund(self, payment_id: str, amount_paise: int) -> str:
        refund = self._client.payment.refund(payment_id, {"amount": amount_paise})
        refund_id = refund["id"]
        initiated_at = datetime.fromtimestamp(refund["created_at"], tz=timezone.utc)
        processed = refund["status"] == "processed"

        self._session.add(
            Refund(
                id=refund_id,
                payment_id=payment_id,
                amount_paise=refund["amount"],
                gateway_status=RefundGatewayStatus.PROCESSED if processed else RefundGatewayStatus.INITIATED,
                initiated_at=initiated_at,
                settled_at=initiated_at if processed else None,
            )
        )

        # Only mark the payment fully REFUNDED once its cumulative refunded
        # amount actually covers the whole captured amount — a partial refund
        # (e.g. capped by test-account balance) leaves money still outstanding.
        payment_after = self._client.payment.fetch(payment_id)
        if payment_after["amount_refunded"] >= payment_after["amount"]:
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
                            amount=refund["amount"],
                            status=refund["status"],
                        )
                    }
                },
            )
        )
        return refund_id

    async def retry_refund_settlement(self, refund_id: str) -> None:
        """Razorpay refunds cannot be force-settled; this re-checks the real
        status and lets the caller act on it. Unlike the mock, this does not
        unconditionally mark the refund processed."""
        refund = self._client.refund.fetch(refund_id)
        if refund["status"] != "processed":
            raise RuntimeError(f"refund {refund_id!r} is still {refund['status']!r}, not yet processed")

        local_refund = await self._session.get(Refund, refund_id)
        if local_refund is not None:
            local_refund.gateway_status = RefundGatewayStatus.PROCESSED
            local_refund.settled_at = datetime.now(timezone.utc)
            await self._session.flush()

    def verify_webhook_signature(self, payload_body: str, signature: str, webhook_secret: str) -> bool:
        try:
            self._client.utility.verify_webhook_signature(payload_body, signature, webhook_secret)
            return True
        except razorpay.errors.SignatureVerificationError:
            return False
