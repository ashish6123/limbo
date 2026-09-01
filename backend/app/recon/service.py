from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.models.enums import OrderState
from app.models.order import Order
from app.models.payment import Payment
from app.models.recon_result import ReconciliationResult
from app.models.refund import Refund
from app.recon.classifier import classify
from app.time_provider import SystemTimeProvider, TimeProvider


async def latest_results_by_order(session: AsyncSession, order_ids: list[str] | None = None) -> dict[str, ReconciliationResult]:
    """Reconciliation results accumulate as a history (one row per classification
    change), so any consumer acting on classification — the policy engine, the
    dashboard's limbo queue — must look at each order's most recent row, never
    the full history, or it will act on a stale, superseded classification.
    Pass order_ids to scope to specific orders, or omit for every order.

    Ranks with a window function instead of pulling every history row into
    Python: with reconciliation run repeatedly (every scheduler cycle, every
    eval batch, every verification script), that history can reach hundreds of
    thousands of rows — selecting and deduping all of them here made this query
    take 2+ seconds and intermittently time out the dashboard."""
    rank = (
        func.row_number()
        .over(
            partition_by=ReconciliationResult.order_id,
            order_by=(ReconciliationResult.classified_at.desc(), ReconciliationResult.id.desc()),
        )
        .label("rank")
    )
    ranked_query = select(ReconciliationResult.id, rank)
    if order_ids is not None:
        ranked_query = ranked_query.where(ReconciliationResult.order_id.in_(order_ids))
    ranked = ranked_query.subquery()

    query = (
        select(ReconciliationResult)
        .join(ranked, ReconciliationResult.id == ranked.c.id)
        .where(ranked.c.rank == 1)
    )

    rows = (await session.execute(query)).scalars().all()
    return {row.order_id: row for row in rows}


class ReconciliationEngine:
    """The scheduled diff job: classifies every order that has a payment record
    or reached a fulfilled state, and persists the result. Pure orders with no
    payment and no fulfillment (abandoned carts) have nothing to reconcile and
    are skipped."""

    def __init__(
        self,
        session: AsyncSession,
        fulfillable_window: timedelta,
        refund_sla: timedelta,
        time_provider: TimeProvider = SystemTimeProvider(),
    ):
        self._session = session
        self._fulfillable_window = fulfillable_window
        self._refund_sla = refund_sla
        self._time = time_provider

    async def run(self, order_ids: list[str] | None = None) -> list[ReconciliationResult]:
        """Pass order_ids to scope this cycle to specific orders — used by the
        demo's on-demand accelerated cycle, which runs under a fast-forwarded
        TimeProvider that must not be applied to every other order in the DB."""
        candidates_query = select(Order.id).where(
            Order.id.in_(select(Payment.order_id)) | Order.state.in_((OrderState.CONFIRMED, OrderState.FULFILLED))
        )
        if order_ids is not None:
            candidates_query = candidates_query.where(Order.id.in_(order_ids))
        candidate_order_ids = (await self._session.execute(candidates_query)).scalars().all()

        orders = {
            o.id: o
            for o in (await self._session.execute(select(Order).where(Order.id.in_(candidate_order_ids)))).scalars()
        }
        payments_by_order: dict[str, list[Payment]] = {}
        for p in (await self._session.execute(select(Payment).where(Payment.order_id.in_(candidate_order_ids)))).scalars():
            payments_by_order.setdefault(p.order_id, []).append(p)

        all_payment_ids = [p.id for payments in payments_by_order.values() for p in payments]
        refunds_by_payment: dict[str, list[Refund]] = {}
        for r in (await self._session.execute(select(Refund).where(Refund.payment_id.in_(all_payment_ids)))).scalars():
            refunds_by_payment.setdefault(r.payment_id, []).append(r)

        previous = await latest_results_by_order(self._session, candidate_order_ids)

        now = self._time.now()
        results: list[ReconciliationResult] = []

        for order_id in candidate_order_ids:
            order = orders[order_id]
            payments = payments_by_order.get(order_id, [])
            refunds = [r for p in payments for r in refunds_by_payment.get(p.id, [])]

            state = classify(order, payments, refunds, now, self._fulfillable_window, self._refund_sla)
            if state is None:
                continue

            prior = previous.get(order_id)
            if prior is not None and prior.state == state:
                continue  # unchanged since last cycle — writing again would only bloat history for no signal

            payment_id = payments[0].id if payments else None
            result = ReconciliationResult(
                order_id=order_id,
                payment_id=payment_id,
                state=state,
                classified_at=now,
            )
            self._session.add(result)
            results.append(result)

            await audit.record(
                self._session,
                stage="detection",
                event=state.value,
                order_id=order_id,
                payment_id=payment_id,
                inputs={"order_state": order.state.value, "prior_state": prior.state.value if prior else None},
                reasoning=f"classified {state.value}" + (f" (was {prior.state.value})" if prior else " (first classification)"),
                created_at=now,
            )

        await self._session.flush()
        return results
