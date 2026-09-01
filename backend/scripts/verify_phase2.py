"""Phase 2 verification: run 50 transactions with a 30% chaos injection rate
across all six failure classes, then assert each class's resulting DB footprint
matches what that failure class should produce.

Run with Postgres up (docker-compose up postgres) and DATABASE_URL pointed at it:
    python -m scripts.verify_phase2
"""

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.chaos.enums import FailureClass
from app.config import settings
from app.models.base import Base, async_session, engine
from app.models.enums import OrderState, PaymentGatewayStatus, RefundGatewayStatus
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.webhook_event import WebhookEvent
from app.time_provider import FakeTimeProvider
import app.models  # noqa: F401
from scripts.chaos_batch import run_chaos_batch

TRANSACTION_COUNT = 50
INJECTION_RATE = 0.30
INJECTED_COUNT = round(TRANSACTION_COUNT * INJECTION_RATE)


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    time_provider = FakeTimeProvider()

    async with async_session() as session:
        transactions = await run_chaos_batch(session, time_provider, TRANSACTION_COUNT, INJECTION_RATE)
        await session.commit()

        order_ids = [order_id for order_id, _ in transactions]
        orders = {o.id: o for o in (await session.execute(select(Order).where(Order.id.in_(order_ids)))).scalars()}
        payments = {p.order_id: p for p in (await session.execute(select(Payment).where(Payment.order_id.in_(order_ids)))).scalars()}
        payment_ids = [p.id for p in payments.values()]
        webhook_events_by_payment: dict[str, list[WebhookEvent]] = {}
        for we in (await session.execute(select(WebhookEvent).where(WebhookEvent.payment_id.in_(payment_ids)))).scalars():
            webhook_events_by_payment.setdefault(we.payment_id, []).append(we)
        refunds_by_payment: dict[str, list[Refund]] = {}
        for r in (await session.execute(select(Refund).where(Refund.payment_id.in_(payment_ids)))).scalars():
            refunds_by_payment.setdefault(r.payment_id, []).append(r)

    failures = []

    def check(condition: bool, description: str) -> None:
        if not condition:
            failures.append(description)

    for order_id, injected in transactions:
        order = orders[order_id]
        payment = payments[order_id]
        events = webhook_events_by_payment.get(payment.id, [])

        check(payment.gateway_status == PaymentGatewayStatus.CAPTURED, f"{order_id}: payment not CAPTURED")

        if injected is None:
            check(order.state == OrderState.CONFIRMED, f"{order_id} (healthy): order not CONFIRMED")
            check(len(events) == 1 and events[0].delivered, f"{order_id} (healthy): expected exactly 1 delivered webhook event")

        elif injected == FailureClass.WEBHOOK_DROPPED:
            check(order.state == OrderState.CANCELLED, f"{order_id} (dropped): order not CANCELLED")
            check(len(events) == 0, f"{order_id} (dropped): expected zero webhook events, found {len(events)}")

        elif injected == FailureClass.WEBHOOK_MERCHANT_500:
            check(order.state == OrderState.CANCELLED, f"{order_id} (500): order not CANCELLED")
            check(
                len(events) == 1 and events[0].merchant_response_code == 500 and not events[0].delivered,
                f"{order_id} (500): expected exactly 1 failed (code 500) webhook event",
            )

        elif injected == FailureClass.WEBHOOK_DELAYED:
            check(order.state == OrderState.CANCELLED, f"{order_id} (delayed): order not CANCELLED")
            check(
                len(events) == 1 and events[0].delivered,
                f"{order_id} (delayed): expected exactly 1 delivered (but late) webhook event",
            )
            if events:
                lag = events[0].delivered_at - payment.captured_at
                check(
                    lag >= timedelta(minutes=settings.order_fulfillable_window_minutes),
                    f"{order_id} (delayed): webhook arrived within the fulfillable window, not after it",
                )

        elif injected == FailureClass.WEBHOOK_OUT_OF_ORDER:
            check(order.state == OrderState.CONFIRMED, f"{order_id} (out of order): order not CONFIRMED")
            check(len(events) == 2, f"{order_id} (out of order): expected 2 webhook events, found {len(events)}")
            by_attempt = {e.attempt_number: e for e in events}
            check(
                1 in by_attempt and 2 in by_attempt and by_attempt[1].delivered_at > by_attempt[2].delivered_at,
                f"{order_id} (out of order): attempt 1 did not arrive after attempt 2",
            )

        elif injected == FailureClass.WEBHOOK_DUPLICATED:
            check(order.state == OrderState.CONFIRMED, f"{order_id} (duplicated): order not CONFIRMED")
            check(len(events) == 2, f"{order_id} (duplicated): expected 2 webhook events, found {len(events)}")
            by_attempt = {e.attempt_number: e for e in events}
            check(
                1 in by_attempt and 2 in by_attempt and by_attempt[2].delivered_at > by_attempt[1].delivered_at,
                f"{order_id} (duplicated): attempt 2 did not arrive after attempt 1",
            )

        elif injected == FailureClass.REFUND_STUCK:
            check(order.state == OrderState.CONFIRMED, f"{order_id} (refund stuck): order not CONFIRMED")
            refunds = refunds_by_payment.get(payment.id, [])
            check(len(refunds) == 1, f"{order_id} (refund stuck): expected exactly 1 refund, found {len(refunds)}")
            if refunds:
                check(
                    refunds[0].gateway_status == RefundGatewayStatus.INITIATED and refunds[0].settled_at is None,
                    f"{order_id} (refund stuck): refund should be INITIATED with no settled_at",
                )

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)

    class_counts = {c: sum(1 for _, i in transactions if i == c) for c in FailureClass}
    healthy_count = sum(1 for _, i in transactions if i is None)
    print(f"PASS: {TRANSACTION_COUNT} transactions, {INJECTED_COUNT} injected ({INJECTION_RATE:.0%}), {healthy_count} healthy")
    for c, count in class_counts.items():
        print(f"  {c.value}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
