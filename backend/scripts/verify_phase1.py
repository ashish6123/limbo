"""Phase 1 verification: create 10 orders, pay each in full via the mock gateway,
and confirm every order ends up CONFIRMED with exactly one CAPTURED payment.

Run with Postgres up (docker-compose up postgres) and DATABASE_URL pointed at it:
    python -m scripts.verify_phase1
"""

import asyncio
import uuid

from sqlalchemy import select

from app.gateway.mock import MockGatewayAdapter
from app.merchant.service import MerchantService
from app.models.base import Base, async_session, engine
from app.models.enums import OrderState, PaymentGatewayStatus
from app.models.order import Order
from app.models.payment import Payment
import app.models  # noqa: F401

ORDER_COUNT = 10
AMOUNT_PAISE = 150_00


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        merchant = MerchantService(session)
        gateway = MockGatewayAdapter(session, webhook_sink=merchant.receive_webhook)

        order_ids = []
        for _ in range(ORDER_COUNT):
            order_id = f"order_{uuid.uuid4().hex[:12]}"
            await merchant.create_order(order_id, AMOUNT_PAISE)
            await gateway.capture_payment(order_id, AMOUNT_PAISE, method="upi")
            order_ids.append(order_id)

        await session.commit()

        orders = (
            await session.execute(select(Order).where(Order.id.in_(order_ids)))
        ).scalars().all()
        payments = (
            await session.execute(select(Payment).where(Payment.order_id.in_(order_ids)))
        ).scalars().all()

    assert len(orders) == ORDER_COUNT, f"expected {ORDER_COUNT} orders, found {len(orders)}"
    not_confirmed = [o.id for o in orders if o.state != OrderState.CONFIRMED]
    assert not not_confirmed, f"orders not CONFIRMED: {not_confirmed}"

    assert len(payments) == ORDER_COUNT, f"expected {ORDER_COUNT} payments, found {len(payments)}"
    not_captured = [p.id for p in payments if p.gateway_status != PaymentGatewayStatus.CAPTURED]
    assert not not_captured, f"payments not CAPTURED: {not_captured}"

    print(f"PASS: {ORDER_COUNT}/{ORDER_COUNT} orders CONFIRMED, {ORDER_COUNT}/{ORDER_COUNT} payments CAPTURED")


if __name__ == "__main__":
    asyncio.run(main())
