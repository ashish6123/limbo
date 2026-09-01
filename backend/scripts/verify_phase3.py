"""Phase 3 verification: run the Phase 2 chaos batch, then reconcile it and check
that classification precision/recall against the chaos injection ground truth is
100% — it must be, since both recon and chaos injection are deterministic.

Run with Postgres up (docker-compose up postgres) and DATABASE_URL pointed at it:
    python -m scripts.verify_phase3
"""

import asyncio
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from app.chaos.enums import FailureClass
from app.config import settings
from app.models.base import Base, async_session, engine
from app.models.chaos_injection import ChaosInjection
from app.models.recon_result import ReconciliationResult
from app.recon.enums import TransactionState
from app.recon.service import ReconciliationEngine
from app.time_provider import FakeTimeProvider
import app.models  # noqa: F401
from scripts.chaos_batch import run_chaos_batch

TRANSACTION_COUNT = 50
INJECTION_RATE = 0.30

# What each chaos class should resolve to at the order/payment/refund level. Note
# WEBHOOK_OUT_OF_ORDER and WEBHOOK_DUPLICATED both still end CONFIRMED with one
# captured payment — the anomaly is only visible in webhook_events, which recon
# does not look at (see docs/decisions.md ADR-007). That is the correct call: no
# recovery action is needed for either.
EXPECTED_STATE = {
    None: TransactionState.MATCHED_HEALTHY,
    FailureClass.WEBHOOK_DROPPED: TransactionState.LIMBO_PAID_CANCELLED,
    FailureClass.WEBHOOK_MERCHANT_500: TransactionState.LIMBO_PAID_CANCELLED,
    FailureClass.WEBHOOK_DELAYED: TransactionState.LIMBO_PAID_CANCELLED,
    FailureClass.WEBHOOK_OUT_OF_ORDER: TransactionState.MATCHED_HEALTHY,
    FailureClass.WEBHOOK_DUPLICATED: TransactionState.MATCHED_HEALTHY,
    FailureClass.REFUND_STUCK: TransactionState.REFUND_STUCK,
}


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    time_provider = FakeTimeProvider()

    async with async_session() as session:
        transactions = await run_chaos_batch(session, time_provider, TRANSACTION_COUNT, INJECTION_RATE)
        await session.commit()

        fulfillable_window = timedelta(minutes=settings.order_fulfillable_window_minutes)
        refund_sla = timedelta(hours=settings.refund_settlement_sla_hours)

        # The chaos batch only fast-forwards past the (short) fulfillable window, not
        # the (long) refund SLA. Advance further so a genuinely stuck refund actually
        # breaches its SLA before we ask recon to classify it.
        time_provider.advance(refund_sla + timedelta(hours=1))

        recon = ReconciliationEngine(session, fulfillable_window, refund_sla, time_provider)
        results = await recon.run()
        await session.commit()

        order_ids = [order_id for order_id, _ in transactions]
        actual_injections = {
            ci.order_id: ci.failure_class
            for ci in (await session.execute(select(ChaosInjection).where(ChaosInjection.order_id.in_(order_ids)))).scalars()
        }
        persisted = {r.order_id: r.state for r in (await session.execute(select(ReconciliationResult).where(ReconciliationResult.order_id.in_(order_ids)))).scalars()}

    results_by_order = {r.order_id: r.state for r in results}

    mismatches = []
    failed_order_ids: set[str] = set()
    for order_id, injected in transactions:
        expected = EXPECTED_STATE[injected]
        actual = results_by_order.get(order_id)
        if actual != expected:
            mismatches.append(f"{order_id} (injected={injected}): expected {expected}, got {actual}")
            failed_order_ids.add(order_id)
        if persisted.get(order_id) != actual:
            mismatches.append(f"{order_id}: recon result not persisted correctly")
            failed_order_ids.add(order_id)

    total = len(transactions)
    correct = total - len(failed_order_ids)
    precision_recall = correct / total

    if mismatches:
        print(f"FAIL: {len(mismatches)} mismatch(es), precision/recall = {precision_recall:.1%}")
        for m in mismatches:
            print(f"  - {m}")
        raise SystemExit(1)

    class_breakdown = Counter(results_by_order[order_id] for order_id, _ in transactions)
    print(f"PASS: {total}/{total} correctly classified, precision/recall = 100.0%")
    for state, count in class_breakdown.items():
        print(f"  {state.value}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
