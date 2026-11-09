"""Takes a buyer-approved PayPal sandbox order (from paypal_manual_checkout.py) and
runs one full real recovery lifecycle:

    real capture -> simulated webhook drop -> merchant timeout sweep
    -> reconciliation detects LIMBO_PAID_CANCELLED -> policy decides AUTO_REFUND
    -> REAL refund executed via the PayPal API -> audit trail shown

Usage:
    python -m scripts.verify_paypal <order_id> <paypal_order_id>

Safe to re-run: PayPal-Request-Id makes the capture and refund idempotent, and the
policy engine skips an action that has already executed.
"""

import asyncio
import sys
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from app.config import settings
from app.gateway.paypal_adapter import PayPalAdapter, to_minor_units
from app.merchant.service import MerchantService
from app.models.audit_log import AuditLog
from app.models.base import async_session, engine, Base
from app.models.enums import PaymentGatewayStatus
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.policy.circuit_breaker import CircuitBreaker
from app.policy.engine import PolicyEngine
from app.policy.enums import ActionStatus, ActionType
from app.recon.service import ReconciliationEngine, latest_results_by_order
from app.time_provider import FakeTimeProvider
import app.models  # noqa: F401


async def main(order_id: str, paypal_order_id: str) -> None:
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        raise SystemExit("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set in .env")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=30.0) as http, async_session() as session:
        order = await session.get(Order, order_id)
        if order is None:
            raise SystemExit(f"unknown local order {order_id!r} — run paypal_manual_checkout.py first")

        async def webhook_sink(payload):
            pass  # no public endpoint to receive this; recorded for the ledger only

        gateway = PayPalAdapter(
            session, http, settings.paypal_client_id, settings.paypal_client_secret,
            webhook_sink, currency=settings.paypal_currency,
        )
        capture_id = await gateway.capture_order(paypal_order_id)
        capture = await gateway.fetch_existing_capture(capture_id)
        amount = capture["amount"]
        print(f"Real PayPal capture: id={capture_id}, status={capture['status']!r}, amount={amount['value']} {amount['currency_code']}")
        if capture["status"] not in ("COMPLETED", "REFUNDED"):
            raise SystemExit(f"capture {capture_id} is {capture['status']!r} — cannot proceed")

        refund_amount = to_minor_units(amount["value"])
        captured_at = datetime.fromisoformat(capture["create_time"].replace("Z", "+00:00"))
        time_provider = FakeTimeProvider(start=captured_at)
        if await session.get(Payment, capture_id) is None:
            session.add(
                Payment(
                    id=capture_id,
                    order_id=order_id,
                    amount_paise=refund_amount,
                    gateway_status=PaymentGatewayStatus.CAPTURED,
                    method="paypal",
                    captured_at=captured_at,
                )
            )
            await session.commit()

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

        # The merchant timeout sweep already cancelled this order, so
        # REPLAY_WEBHOOK is no longer viable and AUTO_REFUND is the correct call.
        action = await policy.decide(order_id, state, refund_amount, within_fulfillable_window=False)
        print(f"Policy decided: {action}")
        if action != ActionType.AUTO_REFUND:
            raise SystemExit(f"expected AUTO_REFUND, policy decided {action} — aborting before refunding")

        # FakeTimeProvider re-anchors to the capture time on every run, so advance
        # past any cooldown left by an earlier attempt.
        time_provider.advance(timedelta(hours=1))

        status = await policy.act(
            order_id,
            capture_id,
            ActionType.AUTO_REFUND,
            refund_amount,
            executor=lambda: gateway.create_refund(capture_id, refund_amount),
        )
        await session.commit()
        print(f"Policy action executed: {status.value}")

        if status not in (ActionStatus.EXECUTED, ActionStatus.SKIPPED_IDEMPOTENT):
            raise SystemExit(f"refund did not execute (status={status}) — check policy_actions/audit_log for why")
        if status == ActionStatus.SKIPPED_IDEMPOTENT:
            print("(this run was skipped as a duplicate of an already-executed action — idempotency guardrail confirmed)")

        refund_row = (
            await session.execute(select(Refund).where(Refund.payment_id == capture_id).order_by(Refund.initiated_at.desc()))
        ).scalars().first()
        print(f"Real PayPal refund id: {refund_row.id}, ledger status={refund_row.gateway_status.value}")

        capture_after = await gateway.fetch_existing_capture(capture_id)
        print(f"Real capture after refund: status={capture_after['status']!r}")

        audit_rows = (
            await session.execute(select(AuditLog).where(AuditLog.order_id == order_id).order_by(AuditLog.id))
        ).scalars().all()
        print(f"\nAudit trail ({len(audit_rows)} entries):")
        for row in audit_rows:
            print(f"  [{row.stage}] {row.event} — {row.reasoning or ''}")

    print("\nPASS: one full real PayPal sandbox recovery lifecycle completed.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m scripts.verify_paypal <order_id> <paypal_order_id>")
    asyncio.run(main(sys.argv[1], sys.argv[2]))
