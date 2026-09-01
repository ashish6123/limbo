"""Phase 4 verification: run the Phase 2/3 chaos batch plus a dedicated
within-window cancellation scenario through the policy engine with guardrails
enabled, then check that:
  - LIMBO_PAID_CANCELLED past the fulfillable window is AUTO_REFUND'd,
  - LIMBO_PAID_CANCELLED still within the window is recovered via REPLAY_WEBHOOK,
  - REFUND_STUCK is recovered via RETRIGGER_REFUND,
  - re-running the exact same batch through the policy engine a second time
    never double-executes any action (idempotency).

Run with Postgres up (docker-compose up postgres) and DATABASE_URL pointed at it:
    python -m scripts.verify_phase4
"""

import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.gateway.mock import MockGatewayAdapter
from app.merchant.service import MerchantService
from app.models.base import Base, async_session, engine
from app.models.enums import OrderState, RefundGatewayStatus
from app.models.order import Order
from app.models.payment import Payment
from app.models.policy_action import PolicyAction
from app.models.refund import Refund
from app.policy.circuit_breaker import CircuitBreaker
from app.policy.engine import PolicyEngine
from app.policy.enums import ActionStatus, ActionType
from app.recon.service import ReconciliationEngine, latest_results_by_order
from app.time_provider import FakeTimeProvider
import app.models  # noqa: F401
from scripts.chaos_batch import run_chaos_batch

TRANSACTION_COUNT = 50
INJECTION_RATE = 0.30
REPLAY_SCENARIO_ORDER_ID = f"order_replay_{uuid.uuid4().hex[:8]}"
REPLAY_SCENARIO_AMOUNT_PAISE = 300_00


def build_executor(action_type: ActionType, order_id: str, payment_id: str | None, refund_id: str | None, merchant: MerchantService, gateway: MockGatewayAdapter, amount_paise: int):
    if action_type == ActionType.REPLAY_WEBHOOK:
        return lambda: merchant.force_complete_order(order_id)
    if action_type == ActionType.AUTO_REFUND:
        return lambda: gateway.create_refund(payment_id, amount_paise)
    if action_type == ActionType.RETRIGGER_REFUND:
        return lambda: gateway.retry_refund_settlement(refund_id)
    raise ValueError(f"no executor wired for {action_type}")


async def run_policy_pass(session, policy: PolicyEngine, merchant: MerchantService, gateway: MockGatewayAdapter, time_provider: FakeTimeProvider, order_ids: list[str]) -> list[tuple[str, ActionType, ActionStatus]]:
    fulfillable_window = timedelta(minutes=settings.order_fulfillable_window_minutes)

    recon_rows = await latest_results_by_order(session, order_ids)
    outcomes = []

    for row in recon_rows.values():
        order = await session.get(Order, row.order_id)
        payment = await session.get(Payment, row.payment_id) if row.payment_id else None
        within_window = bool(payment and time_provider.now() - payment.captured_at < fulfillable_window)

        action = await policy.decide(row.order_id, row.state, order.amount_paise, within_window)
        if action is None:
            continue

        refund_id = None
        if action == ActionType.RETRIGGER_REFUND and payment:
            refund = (await session.execute(select(Refund).where(Refund.payment_id == payment.id))).scalars().first()
            refund_id = refund.id if refund else None

        executor = build_executor(action, row.order_id, row.payment_id, refund_id, merchant, gateway, order.amount_paise)
        status = await policy.act(row.order_id, row.payment_id, action, order.amount_paise, executor)
        outcomes.append((row.order_id, action, status))

    return outcomes


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    time_provider = FakeTimeProvider()

    async with async_session() as session:
        transactions = await run_chaos_batch(session, time_provider, TRANSACTION_COUNT, INJECTION_RATE)

        # Dedicated scenario: payment captured, order cancelled by the merchant
        # for an unrelated reason *within* the fulfillable window — this is the
        # one case that should recover via REPLAY_WEBHOOK, not AUTO_REFUND.
        merchant = MerchantService(session, time_provider)

        async def replay_sink(payload):
            pass  # gateway would emit a webhook here; simulate it being lost

        gateway = MockGatewayAdapter(session, webhook_sink=replay_sink, time_provider=time_provider)
        await merchant.create_order(REPLAY_SCENARIO_ORDER_ID, REPLAY_SCENARIO_AMOUNT_PAISE)
        await gateway.capture_payment(REPLAY_SCENARIO_ORDER_ID, REPLAY_SCENARIO_AMOUNT_PAISE, method="upi")
        await merchant.cancel_order(REPLAY_SCENARIO_ORDER_ID)

        await session.commit()

        fulfillable_window = timedelta(minutes=settings.order_fulfillable_window_minutes)
        refund_sla = timedelta(hours=settings.refund_settlement_sla_hours)
        batch_order_ids = [order_id for order_id, _ in transactions] + [REPLAY_SCENARIO_ORDER_ID]

        circuit_breaker = CircuitBreaker(threshold=settings.circuit_breaker_false_intervention_rate)
        policy = PolicyEngine(
            session,
            max_auto_amount_paise=settings.max_auto_amount_paise,
            max_retry_attempts=settings.max_retry_attempts,
            cooldown_base=timedelta(seconds=1),
            circuit_breaker=circuit_breaker,
            dry_run=False,
            time_provider=time_provider,
        )

        # Round 1: reconcile and act at "now" — the order-window sweep has
        # already run (inside run_chaos_batch), but the refund SLA has not, so
        # stuck refunds still read as healthy and the replay scenario (cancelled
        # moments ago) is still within its fulfillable window.
        recon = ReconciliationEngine(session, fulfillable_window, refund_sla, time_provider)
        await recon.run()
        await session.commit()
        first_pass = await run_policy_pass(session, policy, merchant, gateway, time_provider, batch_order_ids)
        await session.commit()

        # Round 2: fast-forward past the refund SLA and reconcile again — this is
        # what actually turns the stuck refunds into REFUND_STUCK.
        time_provider.advance(refund_sla + timedelta(hours=1))
        await recon.run()
        await session.commit()
        first_pass += await run_policy_pass(session, policy, merchant, gateway, time_provider, batch_order_ids)
        await session.commit()

        # Round 3: re-run everything unchanged — this is the idempotency check.
        second_pass = await run_policy_pass(session, policy, merchant, gateway, time_provider, batch_order_ids)
        await session.commit()

        replay_order = await session.get(Order, REPLAY_SCENARIO_ORDER_ID)
        refund_rows = (await session.execute(select(Refund))).scalars().all()
        policy_action_rows = (await session.execute(select(PolicyAction))).scalars().all()

    failures = []

    def check(condition: bool, description: str) -> None:
        if not condition:
            failures.append(description)

    replay_outcomes = [s for oid, a, s in first_pass if oid == REPLAY_SCENARIO_ORDER_ID]
    check(replay_outcomes == [ActionStatus.EXECUTED], f"replay scenario: expected 1 EXECUTED action, got {replay_outcomes}")
    check(replay_order.state == OrderState.CONFIRMED, "replay scenario: order not recovered to CONFIRMED")

    auto_refund_orders = [oid for oid, a, s in first_pass if a == ActionType.AUTO_REFUND and s == ActionStatus.EXECUTED]
    check(len(auto_refund_orders) == 9, f"expected 9 AUTO_REFUND executions (dropped+500+delayed), got {len(auto_refund_orders)}")

    retrigger_orders = [oid for oid, a, s in first_pass if a == ActionType.RETRIGGER_REFUND and s == ActionStatus.EXECUTED]
    check(len(retrigger_orders) == 2, f"expected 2 RETRIGGER_REFUND executions, got {len(retrigger_orders)}")

    settled_stuck_refunds = [r for r in refund_rows if r.gateway_status == RefundGatewayStatus.PROCESSED and r.settled_at is not None]
    check(len(settled_stuck_refunds) >= 2, "stuck refunds were not actually settled by RETRIGGER_REFUND")

    check(len(second_pass) > 0, "second pass produced no decisions at all — test setup is broken")
    second_pass_statuses = {s for _, _, s in second_pass}
    check(
        second_pass_statuses <= {ActionStatus.SKIPPED_IDEMPOTENT},
        f"idempotency violated: second pass produced non-idempotent statuses {second_pass_statuses}",
    )

    executed_count_by_key = {}
    for row in policy_action_rows:
        if row.status == ActionStatus.EXECUTED:
            executed_count_by_key[row.idempotency_key] = executed_count_by_key.get(row.idempotency_key, 0) + 1
    double_executed = {k: v for k, v in executed_count_by_key.items() if v > 1}
    check(not double_executed, f"double-execution detected: {double_executed}")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)

    print("PASS: policy engine guardrails verified")
    print(f"  first pass actions executed: {len(first_pass)}")
    print(f"  second pass (idempotency check): {len(second_pass)} decisions, all SKIPPED_IDEMPOTENT")
    print(f"  replay scenario: order recovered via REPLAY_WEBHOOK")
    print(f"  auto refunds executed: {len(auto_refund_orders)}")
    print(f"  stuck refunds settled via RETRIGGER_REFUND: {len(retrigger_orders)}")


if __name__ == "__main__":
    asyncio.run(main())
