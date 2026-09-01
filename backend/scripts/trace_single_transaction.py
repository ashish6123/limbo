"""Traces exactly one transaction through the full pipeline, printing raw state
at every stage: payment capture, webhook drop, order cancellation, reconciliation
detection, diagnosis, and the policy engine's decision + action. No dashboard —
this is what a panel review asks to see.

Runs in mock mode (GATEWAY_MODE=mock) so it is instant and self-contained. The
mock gateway's create_refund() is the same RazorpayAdapter-shaped interface the
real adapter implements — see docs/decisions.md ADR-004 — but it is not a live
Razorpay API call. A real one was already proven end to end in Phase 6
(pay_TSrHOuMZY7szwc / rfnd_TTAg77RyCFBQrL), printed here as supporting evidence.

Usage:
    python -m scripts.trace_single_transaction
"""

import asyncio
import json
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.chaos.engine import ChaosEngine
from app.chaos.enums import FailureClass
from app.config import settings
from app.diagnosis.service import DiagnosisEngine
from app.gateway.mock import MockGatewayAdapter
from app.merchant.service import MerchantService
from app.models.audit_log import AuditLog
from app.models.base import Base, async_session, engine
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.policy.circuit_breaker import CircuitBreaker
from app.policy.engine import PolicyEngine
from app.recon.service import ReconciliationEngine, latest_results_by_order
from app.time_provider import FakeTimeProvider
import app.models  # noqa: F401

AMOUNT_PAISE = 49_900  # Rs 499.00, matching the flagship story in docs/recovery-flow.md


def hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def dump_row(row) -> str:
    """Prints a SQLAlchemy model instance as a flat dict of column values —
    a raw row, not a formatted report."""
    data = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    for k, v in data.items():
        if hasattr(v, "value"):  # enum columns
            data[k] = v.value
        elif not isinstance(v, (str, int, float, bool, type(None))):
            data[k] = str(v)
    return json.dumps(data, indent=2, default=str)


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    time_provider = FakeTimeProvider()
    order_id = f"order_trace_{uuid.uuid4().hex[:8]}"

    async with async_session() as session:
        merchant = MerchantService(session, time_provider)
        chaos = ChaosEngine(session, time_provider)

        # ---- Step 1: capture the payment -----------------------------------
        await merchant.create_order(order_id, AMOUNT_PAISE)

        async def dropping_sink(payload):
            await chaos.deliver(payload, merchant, FailureClass.WEBHOOK_DROPPED)

        gateway = MockGatewayAdapter(session, webhook_sink=dropping_sink, time_provider=time_provider)
        payment_id = await gateway.capture_payment(order_id, AMOUNT_PAISE, method="upi")
        await session.commit()

        hr("STEP 1 - payments table, immediately after gateway capture")
        payment = await session.get(Payment, payment_id)
        print(dump_row(payment))

        # ---- Step 2: webhook was dropped; merchant times the order out -----
        fulfillable_window = timedelta(minutes=settings.order_fulfillable_window_minutes)
        refund_sla = timedelta(hours=settings.refund_settlement_sla_hours)
        time_provider.advance(fulfillable_window + timedelta(minutes=5))
        cancelled = await merchant.expire_unconfirmed_orders(cutoff=time_provider.now())
        await session.commit()

        hr("STEP 2 - orders table, after webhook drop + merchant timeout sweep")
        order = await session.get(Order, order_id)
        print(dump_row(order))
        print(f"\n(webhook_events rows for this payment: 0 - it was never delivered; merchant cancelled independently)")
        print(f"(order cancelled by timeout sweep: {order_id in cancelled})")

        # ---- Step 3: reconciliation detects the mismatch -------------------
        recon = ReconciliationEngine(session, fulfillable_window, refund_sla, time_provider)
        await recon.run()
        await session.commit()

        hr("STEP 3 - reconciliation engine classification + detection audit entry")
        latest = await latest_results_by_order(session, [order_id])
        recon_result = latest[order_id]
        print(dump_row(recon_result))
        detection_entry = (
            await session.execute(
                select(AuditLog).where(AuditLog.order_id == order_id, AuditLog.stage == "detection")
            )
        ).scalars().first()
        print("\naudit_log entry (stage=detection):")
        print(dump_row(detection_entry))

        # ---- Step 4: diagnosis narrates the root cause from evidence -------
        diagnosis = DiagnosisEngine(
            session,
            fulfillable_window_minutes=settings.order_fulfillable_window_minutes,
            refund_sla_hours=settings.refund_settlement_sla_hours,
            anthropic_api_key=settings.anthropic_api_key,
            time_provider=time_provider,
        )
        result, source = await diagnosis.diagnose(order_id)
        await session.commit()

        hr(f"STEP 4 - diagnosis agent output (source={source})")
        print(json.dumps(result.model_dump(mode="json"), indent=2))

        # ---- Step 5: policy engine decides and acts -------------------------
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
        within_window = time_provider.now() - payment.captured_at < fulfillable_window
        action = await policy.decide(order_id, recon_result.state, AMOUNT_PAISE, within_window)

        hr("STEP 5a - policy engine decision")
        print(f"state={recon_result.state.value}  within_fulfillable_window={within_window}")
        print(f"amount_paise={AMOUNT_PAISE}  ceiling={settings.max_auto_amount_paise}  circuit_breaker_tripped={circuit_breaker.tripped}")
        print(f"decision: {action.value if action else None}")

        call_log: dict = {}

        async def logging_refund_call():
            call_log["gateway"] = "mock (GATEWAY_MODE=mock)"
            call_log["method"] = "MockGatewayAdapter.create_refund"
            call_log["request"] = {"payment_id": payment_id, "amount": AMOUNT_PAISE}
            refund_id = await gateway.create_refund(payment_id, AMOUNT_PAISE)
            call_log["response"] = json.loads(dump_row(await session.get(Refund, refund_id)))

        status = await policy.act(order_id, payment_id, action, AMOUNT_PAISE, logging_refund_call)
        await session.commit()

        hr("STEP 5b - gateway call and outcome")
        print(json.dumps(call_log, indent=2, default=str))
        print(f"\npolicy action status: {status.value}")

        outcome_entry = (
            await session.execute(
                select(AuditLog).where(AuditLog.order_id == order_id, AuditLog.stage == "outcome")
            )
        ).scalars().first()
        print("\naudit_log entry (stage=outcome):")
        print(dump_row(outcome_entry))

        hr("SUPPORTING EVIDENCE - the same code path against the real Razorpay API (Phase 6)")
        print(json.dumps({
            "note": "Not part of this trace run. From scripts/verify_phase6.py against live Razorpay test mode.",
            "real_payment_id": "pay_TSrHOuMZY7szwc",
            "real_refund_id": "rfnd_TTAg77RyCFBQrL",
            "real_refund_status": "processed",
            "real_refund_amount_paise": 500,
            "verified_via": "client.refund.fetch() against api.razorpay.com, see docs/decisions.md ADR-018/019/020",
        }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
