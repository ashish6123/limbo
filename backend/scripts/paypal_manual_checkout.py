"""Creates a real PayPal sandbox order and a matching local order, so a human can
approve it once with a sandbox buyer account (same pattern as Razorpay, ADR-018).

Run:
    python -m scripts.paypal_manual_checkout

Then open the printed URL, log in as your sandbox personal account and pay. The
page you land on afterwards does not need to load; the approval has already gone
through by then.
"""

import asyncio
import uuid

import httpx

from app.config import settings
from app.gateway.paypal_adapter import PayPalAdapter
from app.merchant.service import MerchantService
from app.models.base import Base, async_session, engine
import app.models  # noqa: F401

AMOUNT_MINOR = 10_00
RETURN_URL = "https://example.com/limbo-paypal-return"


async def main() -> None:
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        raise SystemExit("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set in .env")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    order_id = f"order_pp_{uuid.uuid4().hex[:12]}"

    async def webhook_sink(payload):
        pass

    async with httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=30.0) as http, async_session() as session:
        await MerchantService(session).create_order(order_id, AMOUNT_MINOR, currency=settings.paypal_currency)
        await session.commit()

        gateway = PayPalAdapter(
            session, http, settings.paypal_client_id, settings.paypal_client_secret,
            webhook_sink, currency=settings.paypal_currency,
        )
        paypal_order_id, approve_url = await gateway.create_checkout_order(order_id, AMOUNT_MINOR, RETURN_URL)

    print(f"Local order created: {order_id}")
    print(f"Approve this real PayPal sandbox order: {approve_url}")
    print("Log in with your sandbox personal account and pay.")
    print(f"Then run: python -m scripts.verify_paypal {order_id} {paypal_order_id}")


if __name__ == "__main__":
    asyncio.run(main())
