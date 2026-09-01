from datetime import timedelta

import pytest

from app.merchant.service import MerchantService
from app.models.enums import OrderState, PaymentGatewayStatus
from app.models.payment import Payment
from app.recon.enums import TransactionState
from app.recon.service import ReconciliationEngine, latest_results_by_order
from app.time_provider import FakeTimeProvider

WINDOW = timedelta(minutes=30)
REFUND_SLA = timedelta(hours=72)


@pytest.mark.asyncio
async def test_run_with_order_ids_only_classifies_named_orders(session):
    time_provider = FakeTimeProvider()
    merchant = MerchantService(session, time_provider)
    order_a = await merchant.create_order("order_scope_a", 10000)
    order_b = await merchant.create_order("order_scope_b", 10000)
    order_a.state = OrderState.CANCELLED
    order_b.state = OrderState.CANCELLED
    for order_id in ("order_scope_a", "order_scope_b"):
        session.add(
            Payment(
                id=f"pay_{order_id}",
                order_id=order_id,
                amount_paise=10000,
                gateway_status=PaymentGatewayStatus.CAPTURED,
                method="card",
                captured_at=time_provider.now(),
            )
        )
    await session.flush()

    recon = ReconciliationEngine(session, WINDOW, REFUND_SLA, time_provider)
    await recon.run(order_ids=["order_scope_a"])

    latest = await latest_results_by_order(session)
    assert "order_scope_a" in latest
    assert latest["order_scope_a"].state == TransactionState.LIMBO_PAID_CANCELLED
    assert "order_scope_b" not in latest


@pytest.mark.asyncio
async def test_expire_unconfirmed_orders_with_order_ids_only_cancels_named_orders(session):
    time_provider = FakeTimeProvider()
    merchant = MerchantService(session, time_provider)
    await merchant.create_order("order_expire_a", 10000)
    await merchant.create_order("order_expire_b", 10000)
    time_provider.advance(timedelta(hours=1))

    cancelled = await merchant.expire_unconfirmed_orders(cutoff=time_provider.now(), order_ids=["order_expire_a"])

    assert cancelled == ["order_expire_a"]
