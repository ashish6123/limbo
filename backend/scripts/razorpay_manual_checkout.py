"""Phase 6, step 1: create a real Razorpay test-mode Payment Link and a matching
local order, so a human can complete one real checkout. Razorpay has no
supported, non-deprecated way to create a captured payment purely server-side
(see docs/decisions.md ADR-018) — a human paying via real Checkout is the only
route to a genuine test-mode payment.

Run:
    python -m scripts.razorpay_manual_checkout

Then open the printed URL, pay with a Razorpay test card (e.g. 4111 1111 1111
1111, any future expiry, any CVV), and paste the resulting payment id into
scripts/verify_phase6.py's PAYMENT_ID constant.
"""

import asyncio
import uuid

import razorpay

from app.config import settings
from app.merchant.service import MerchantService
from app.models.base import Base, async_session, engine
import app.models  # noqa: F401

AMOUNT_PAISE = 100_00


async def main() -> None:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise SystemExit("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set in .env")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    order_id = f"order_rzp_{uuid.uuid4().hex[:12]}"

    async with async_session() as session:
        merchant = MerchantService(session)
        await merchant.create_order(order_id, AMOUNT_PAISE)
        await session.commit()

    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    link = client.payment_link.create(
        {
            "amount": AMOUNT_PAISE,
            "currency": "INR",
            "description": "Limbo Phase 6 manual test-mode checkout",
            "reference_id": order_id,
            "notify": {"sms": False, "email": False},
        }
    )

    print(f"Local order created: {order_id}")
    print(f"Pay this real Razorpay test-mode link: {link['short_url']}")
    print("Use a test card (e.g. 4111 1111 1111 1111, any future expiry, any CVV).")
    print("After paying, copy the payment id from the confirmation page or your Razorpay dashboard,")
    print(f"then run: python -m scripts.verify_phase6 {order_id} <payment_id>")


if __name__ == "__main__":
    asyncio.run(main())
