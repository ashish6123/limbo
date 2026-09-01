import asyncio
import logging
from datetime import timedelta

from app.config import Settings
from app.diagnosis.service import DiagnosisEngine
from app.gateway.factory import build_gateway_adapter
from app.merchant.service import MerchantService
from app.models.base import async_session
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.policy.circuit_breaker import CircuitBreaker
from app.policy.engine import PolicyEngine
from app.policy.enums import ActionType
from app.recon.service import ReconciliationEngine, latest_results_by_order
from app.time_provider import SystemTimeProvider
from sqlalchemy import select, text

logger = logging.getLogger("limbo.scheduler")

_ACTIONABLE_STATES = {"LIMBO_PAID_CANCELLED", "REFUND_STUCK", "DUPLICATE_PAYMENT", "INVERSE_LEAK"}

# Postgres session-level advisory lock key, arbitrary but fixed: the deployed
# scheduler runs as its own Render Background Worker, separate from the web
# process, so overlapping cycles from two instances during a deploy must be
# prevented at the DB level rather than assumed away by "only one process".
_SCHEDULER_LOCK_KEY = 923_110_006


async def run_reconciliation_cycle(settings: Settings) -> None:
    """One cycle of the continuous reconciliation loop: classify, diagnose, and
    act on whatever the current DB state shows. This is what makes Limbo an
    always-on system rather than a one-shot script — see ADR-006."""
    time_provider = SystemTimeProvider()
    fulfillable_window = timedelta(minutes=settings.order_fulfillable_window_minutes)
    refund_sla = timedelta(hours=settings.refund_settlement_sla_hours)

    async with async_session() as session:
        acquired = (
            await session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _SCHEDULER_LOCK_KEY})
        ).scalar()
        if not acquired:
            logger.info("reconciliation cycle skipped: another worker instance holds the lock")
            return
        try:
            await _run_reconciliation_cycle_locked(session, settings, time_provider, fulfillable_window, refund_sla)
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _SCHEDULER_LOCK_KEY})


async def _run_reconciliation_cycle_locked(session, settings: Settings, time_provider, fulfillable_window, refund_sla) -> None:
    merchant = MerchantService(session, time_provider)
    await merchant.expire_unconfirmed_orders(cutoff=time_provider.now() - fulfillable_window)
    await session.commit()

    recon = ReconciliationEngine(session, fulfillable_window, refund_sla, time_provider)
    await recon.run()
    await session.commit()

    latest = await latest_results_by_order(session)
    actionable = {oid: r for oid, r in latest.items() if r.state.value in _ACTIONABLE_STATES}
    if not actionable:
        return

    diagnosis = DiagnosisEngine(
        session,
        fulfillable_window_minutes=settings.order_fulfillable_window_minutes,
        refund_sla_hours=settings.refund_settlement_sla_hours,
        anthropic_api_key=settings.anthropic_api_key,
        time_provider=time_provider,
    )
    for order_id in actionable:
        await diagnosis.diagnose(order_id)
    await session.commit()

    async def webhook_sink(payload):
        pass  # completion webhooks from policy-triggered gateway calls are not re-delivered to ourselves

    gateway = build_gateway_adapter(settings, session, webhook_sink, time_provider)
    circuit_breaker = CircuitBreaker(threshold=settings.circuit_breaker_false_intervention_rate)
    policy = PolicyEngine(
        session,
        max_auto_amount_paise=settings.max_auto_amount_paise,
        max_retry_attempts=settings.max_retry_attempts,
        cooldown_base=timedelta(seconds=settings.recon_interval_seconds),
        circuit_breaker=circuit_breaker,
        dry_run=settings.dry_run,
        time_provider=time_provider,
    )

    for order_id, result in actionable.items():
        order = await session.get(Order, order_id)
        payment = (
            await session.execute(select(Payment).where(Payment.order_id == order_id))
        ).scalars().first()
        within_window = bool(payment and time_provider.now() - payment.captured_at < fulfillable_window)

        action = await policy.decide(order_id, result.state, order.amount_paise, within_window)
        if action is None:
            continue

        executor = await build_executor(action, order_id, payment, order.amount_paise, merchant, gateway, session)
        await policy.act(order_id, payment.id if payment else None, action, order.amount_paise, executor)

    await session.commit()


async def _noop():
    pass


async def build_executor(action: ActionType, order_id: str, payment, amount_paise: int, merchant: MerchantService, gateway, session):
    """Builds the PolicyEngine executor for a decided action. Shared by the live
    scheduler and the demo module's on-demand accelerated cycle."""
    if action == ActionType.REPLAY_WEBHOOK:
        return lambda: merchant.force_complete_order(order_id)
    if action in (ActionType.AUTO_REFUND, ActionType.DEDUP_REFUND):
        return lambda: gateway.create_refund(payment.id, amount_paise)
    if action == ActionType.RETRIGGER_REFUND:
        refund = (await session.execute(select(Refund).where(Refund.payment_id == payment.id))).scalars().first()
        return (lambda: gateway.retry_refund_settlement(refund.id)) if refund else _noop
    return _noop


async def run_scheduler(settings: Settings) -> None:
    """Runs run_reconciliation_cycle forever on RECON_INTERVAL_SECONDS. Errors in
    one cycle are logged and do not kill the loop — a stuck transaction should
    not take down continuous reconciliation for every other transaction."""
    while True:
        try:
            await run_reconciliation_cycle(settings)
        except Exception:
            logger.exception("reconciliation cycle failed")
        await asyncio.sleep(settings.recon_interval_seconds)
