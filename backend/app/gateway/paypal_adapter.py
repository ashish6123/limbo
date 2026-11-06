import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.base import GatewayAdapter, WebhookSink
from app.gateway.schemas import RefundEntity, WebhookPayload
from app.models.enums import PaymentGatewayStatus, RefundGatewayStatus, WebhookEventType
from app.models.payment import Payment
from app.models.refund import Refund

# PayPal refund statuses (Payments v2) mapped onto Limbo's ledger statuses.
_REFUND_STATUS = {
    "COMPLETED": RefundGatewayStatus.PROCESSED,
    "PENDING": RefundGatewayStatus.INITIATED,
    "FAILED": RefundGatewayStatus.FAILED,
    "CANCELLED": RefundGatewayStatus.FAILED,
}

# Refresh the OAuth token this many seconds before PayPal says it expires.
_TOKEN_EXPIRY_MARGIN_SECONDS = 60


def to_minor_units(value: str) -> int:
    """PayPal amounts are decimal strings ("10.00"); Limbo's ledger stores integer
    minor units in the *_paise columns (cents for USD, paise for INR)."""
    return int((Decimal(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_decimal_string(minor_units: int) -> str:
    return f"{Decimal(minor_units) / 100:.2f}"


class PayPalAdapter(GatewayAdapter):
    """PayPal sandbox adapter over the REST API (OAuth2 client credentials,
    Payments v2 refunds, webhook signature verification).

    Limbo's payment id for a PayPal payment is the *capture* id, not the order
    id: refunds and status lookups in Payments v2 are all keyed on the capture.

    capture_payment stays unimplemented, as with Razorpay in ADR-018: a buyer has
    to approve an order before it can be captured. create_checkout_order returns
    the approval link, and capture_order captures the order once it is approved."""

    def __init__(
        self,
        session: AsyncSession,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        webhook_sink: WebhookSink,
        currency: str = "USD",
    ):
        self._session = session
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._webhook_sink = webhook_sink
        self._currency = currency
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        response = await self._http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = time.monotonic() + body["expires_in"] - _TOKEN_EXPIRY_MARGIN_SECONDS
        return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {await self._access_token()}", **kwargs.pop("headers", {})}
        response = await self._http.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    async def capture_payment(self, order_id: str, amount_paise: int, method: str) -> str:
        raise NotImplementedError(
            "PayPalAdapter cannot create a captured payment on its own: a buyer has to "
            "approve the order. Use create_checkout_order, then capture_order."
        )

    async def create_checkout_order(self, reference_id: str, amount_paise: int, return_url: str) -> tuple[str, str]:
        """Creates an Orders v2 order. Returns (PayPal order id, buyer approval URL)."""
        order = await self._request(
            "POST",
            "/v2/checkout/orders",
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": reference_id,
                        "amount": {"value": to_decimal_string(amount_paise), "currency_code": self._currency},
                    }
                ],
                "payment_source": {
                    "paypal": {
                        "experience_context": {
                            "user_action": "PAY_NOW",
                            "return_url": return_url,
                            "cancel_url": return_url,
                        }
                    }
                },
            },
            headers={"PayPal-Request-Id": f"{reference_id}:order"},
        )
        approve_url = next(link["href"] for link in order["links"] if link["rel"] in ("payer-action", "approve"))
        return order["id"], approve_url

    async def capture_order(self, paypal_order_id: str) -> str:
        """Captures a buyer-approved order. Returns the capture id, which is Limbo's
        payment id for PayPal. Does not write to our ledger, like fetch_existing_capture."""
        order = await self._request(
            "POST",
            f"/v2/checkout/orders/{paypal_order_id}/capture",
            json={},
            headers={"PayPal-Request-Id": f"{paypal_order_id}:capture"},
        )
        return order["purchase_units"][0]["payments"]["captures"][0]["id"]

    async def fetch_existing_capture(self, capture_id: str) -> dict:
        """Brings a real, externally-created sandbox capture into view. Does not
        write to our ledger — the caller decides how to record it."""
        return await self._request("GET", f"/v2/payments/captures/{capture_id}")

    async def create_refund(self, payment_id: str, amount_paise: int) -> str:
        refund = await self._request(
            "POST",
            f"/v2/payments/captures/{payment_id}/refund",
            json={"amount": {"value": to_decimal_string(amount_paise), "currency_code": self._currency}},
            # Same {order_id}:{action_type}-style idempotency the policy engine
            # enforces locally, also enforced on PayPal's side.
            headers={"PayPal-Request-Id": f"{payment_id}:refund:{amount_paise}"},
        )
        refund_id = refund["id"]
        status = _REFUND_STATUS.get(refund["status"], RefundGatewayStatus.INITIATED)
        # The create-refund response may omit amount and create_time unless
        # Prefer: return=representation is set, so fall back to what we asked for.
        refunded_minor = to_minor_units(refund["amount"]["value"]) if "amount" in refund else amount_paise
        initiated_at = (
            datetime.fromisoformat(refund["create_time"].replace("Z", "+00:00"))
            if "create_time" in refund
            else datetime.now(timezone.utc)
        )
        processed = status == RefundGatewayStatus.PROCESSED

        self._session.add(
            Refund(
                id=refund_id,
                payment_id=payment_id,
                amount_paise=refunded_minor,
                gateway_status=status,
                initiated_at=initiated_at,
                settled_at=initiated_at if processed else None,
            )
        )

        # Only a fully refunded capture marks the payment REFUNDED; a partial
        # refund leaves money outstanding (same rule as ADR-020).
        capture_after = await self.fetch_existing_capture(payment_id)
        if capture_after["status"] == "REFUNDED":
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
                            amount=refunded_minor,
                            status=status.value,
                        )
                    }
                },
            )
        )
        return refund_id

    async def retry_refund_settlement(self, refund_id: str) -> None:
        """PayPal refunds cannot be force-settled; this re-checks the real status
        and lets the caller act on it, like the Razorpay adapter."""
        refund = await self._request("GET", f"/v2/payments/refunds/{refund_id}")
        if _REFUND_STATUS.get(refund["status"]) != RefundGatewayStatus.PROCESSED:
            raise RuntimeError(f"refund {refund_id!r} is still {refund['status']!r}, not yet completed")

        local_refund = await self._session.get(Refund, refund_id)
        if local_refund is not None:
            local_refund.gateway_status = RefundGatewayStatus.PROCESSED
            local_refund.settled_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def verify_webhook_signature(self, headers: dict[str, str], event: dict, webhook_id: str) -> bool:
        """PayPal webhooks are verified by PayPal itself, not with a local HMAC:
        the transmission headers plus the raw event go to verify-webhook-signature.
        Header lookup is case-insensitive, since frameworks normalise them differently."""
        lowered = {k.lower(): v for k, v in headers.items()}
        try:
            body = await self._request(
                "POST",
                "/v1/notifications/verify-webhook-signature",
                json={
                    "auth_algo": lowered["paypal-auth-algo"],
                    "cert_url": lowered["paypal-cert-url"],
                    "transmission_id": lowered["paypal-transmission-id"],
                    "transmission_sig": lowered["paypal-transmission-sig"],
                    "transmission_time": lowered["paypal-transmission-time"],
                    "webhook_id": webhook_id,
                    "webhook_event": event,
                },
            )
        except (KeyError, httpx.HTTPStatusError):
            return False
        return body.get("verification_status") == "SUCCESS"
