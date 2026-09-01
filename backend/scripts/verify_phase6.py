"""Phase 6 verification: takes a real, already-paid Razorpay test-mode payment
(from scripts/razorpay_manual_checkout.py + a human completing checkout) and
runs it through one full real recovery lifecycle:

    real payment -> simulated webhook drop -> merchant timeout sweep
    -> reconciliation detects LIMBO_PAID_CANCELLED -> policy decides AUTO_REFUND
    -> REAL refund executed via the Razorpay API -> audit trail shown

Usage:
    python -m scripts.verify_phase6 <order_id> <payment_id>

<order_id> is the local order id printed by razorpay_manual_checkout.py.
<payment_id> is the real Razorpay payment id from the paid checkout.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import razorpay
from sqlalchemy import select

from app.config import settings
from app.gateway.razorpay_adapter import RazorpayAdapter
from app.merchant.service import MerchantService
from app.models.audit_log import AuditLog
from app.models.base import async_session, engine, Base
from app.models.enums import OrderState, PaymentGatewayStatus
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.policy.circuit_breaker import CircuitBreaker
from app.policy.engine import PolicyEngine
from app.policy.enums import ActionStatus, ActionType
from app.recon.service import ReconciliationEngine, latest_results_by_order
from app.time_provider import FakeTimeProvider
import app.models  # noqa: F401


async def main(order_id: str, payment_id: str) -> None:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise SystemExit("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set in .env")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if order is None:
            raise SystemExit(f"unknown local order {order_id!r} — run razorpay_manual_checkout.py first")

        async def webhook_sink(payload):
            pass  # no public endpoint to actually receive this; recorded for the ledger only

        gateway = RazorpayAdapter(session, client, webhook_sink)
        real_payment = await gateway.fetch_existing_payment(payment_id)
        print(f"Real Razorpay payment fetched: status={real_payment['status']!r}, amount={real_payment['amount']}")
        if real_payment["status"] != "captured":
            raise SystemExit(f"payment {payment_id} is {real_payment['status']!r}, not captured — cannot proceed")

        captured_at = datetime.fromtimestamp(real_payment["created_at"], tz=timezone.utc)
        time_provider = FakeTimeProvider(start=captured_at)
        if await session.get(Payment, payment_id) is None:
            session.add(
                Payment(
                    id=payment_id,
                    order_id=order_id,
                    amount_paise=real_payment["amount"],
                    gateway_status=PaymentGatewayStatus.CAPTURED,
                    method=real_payment["method"],
                    captured_at=captured_at,
                )
            )
            await session.commit()

        # This test account cannot fund a full refund of a real captured payment
        # (Razorpay validates refunds against actual account balance, even in
        # test mode) — see docs/decisions.md ADR-019. Refund whatever is both
        # still unrefunded on the payment and safely within a small margin.
        remaining_unrefunded = real_payment["amount"] - real_payment["amount_refunded"]
        refund_amount = min(remaining_unrefunded, 500)
        if refund_amount <= 0:
            raise SystemExit(f"payment {payment_id} has nothing left to refund (amount_refunded={real_payment['amount_refunded']})")
        print(f"Refunding {refund_amount} paise (capped by test-account balance constraints, see ADR-019)")

        # Simulated webhook drop: no webhook_events row is written, order stays
        # AWAITING_PAYMENT until the merchant's own timeout sweep runs.
        merchant = MerchantService(session, time_provider)
        fulfillable_window = timedelta(minutes=settings.order_fulfillable_window_minutes)
        refund_sla = timedelta(hours=settings.refund_settlement_sla_hours)
        time_provider.advance(fulfillable_window + timedelta(minutes=5))
        cancelled = await merchant.expire_unconfirmed_orders(cutoff=time_provider.now())
        await session.commit()
        print(f"Merchant timeout sweep cancelled: {cancelled}")

        recon = ReconciliationEngine(session, fulfillable_window, refund_sla, time_provider)
        await recon.run()
        await session.commit()

        latest = await latest_results_by_order(session, [order_id])
        state = latest[order_id].state
        print(f"Reconciliation classified order as: {state.value}")

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

        # The merchant timeout sweep already cancelled this order after the
        # fulfillable window elapsed, so REPLAY_WEBHOOK is no longer viable —
        # AUTO_REFUND is the correct call here regardless of window math.
        action = await policy.decide(order_id, state, refund_amount, within_fulfillable_window=False)
        print(f"Policy decided: {action}")
        if action != ActionType.AUTO_REFUND:
            raise SystemExit(f"expected AUTO_REFUND, policy decided {action} — aborting before touching real money")

        # FakeTimeProvider re-anchors to the real payment's fixed capture time on
        # every run, so a cooldown started by an earlier attempt (in a previous
        # invocation of this script) would otherwise never appear to have
        # elapsed. Advance well past any plausible cooldown before retrying.
        time_provider.advance(timedelta(hours=1))

        status = await policy.act(
            order_id,
            payment_id,
            ActionType.AUTO_REFUND,
            refund_amount,
            executor=lambda: gateway.create_refund(payment_id, refund_amount),
        )
        await session.commit()
        print(f"Policy action executed: {status.value}")

        if status not in (ActionStatus.EXECUTED, ActionStatus.SKIPPED_IDEMPOTENT):
            raise SystemExit(f"refund did not execute (status={status}) — check policy_actions/audit_log for why")
        if status == ActionStatus.SKIPPED_IDEMPOTENT:
            print("(this run was skipped as a duplicate of an already-executed action — idempotency guardrail confirmed)")

        refund_row = (
            await session.execute(select(Refund).where(Refund.payment_id == payment_id).order_by(Refund.initiated_at.desc()))
        ).scalars().first()
        real_refund = client.refund.fetch(refund_row.id)
        print(f"Real refund confirmed via Razorpay API: id={real_refund['id']}, status={real_refund['status']}, amount={real_refund['amount']}")

        real_payment_after = client.payment.fetch(payment_id)
        print(f"Real payment after refund: amount_refunded={real_payment_after['amount_refunded']}, refund_status={real_payment_after['refund_status']}")

        audit_rows = (
            await session.execute(select(AuditLog).where(AuditLog.order_id == order_id).order_by(AuditLog.id))
        ).scalars().all()
        print(f"\nAudit trail ({len(audit_rows)} entries):")
        for row in audit_rows:
            print(f"  [{row.stage}] {row.event} — {row.reasoning or ''}")

    print("\nPASS: one full real test-mode recovery lifecycle completed.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m scripts.verify_phase6 <order_id> <payment_id>")
    asyncio.run(main(sys.argv[1], sys.argv[2]))
