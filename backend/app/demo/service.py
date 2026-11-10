import secrets
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import razorpay
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.chaos.engine import ChaosEngine
from app.chaos.enums import FailureClass
from app.config import Settings
from app.diagnosis.service import DiagnosisEngine
from app.gateway.factory import build_gateway_adapter
from app.gateway.mock import MockGatewayAdapter
from app.gateway.paypal_adapter import PayPalAdapter, to_minor_units
from app.gateway.schemas import PaymentEntity, WebhookPayload
from app.merchant.service import MerchantService
from app.models.chaos_injection import ChaosInjection
from app.models.demo_session import DemoSession
from app.models.diagnosis_record import DiagnosisRecord
from app.models.enums import PaymentGatewayStatus, WebhookEventType
from app.models.order import Order
from app.models.payment import Payment
from app.models.policy_action import PolicyAction
from app.models.recon_result import ReconciliationResult
from app.models.refund import Refund
from app.models.webhook_event import WebhookEvent
from app.policy.circuit_breaker import CircuitBreaker
from app.policy.engine import PolicyEngine
from app.recon.service import ReconciliationEngine, latest_results_by_order
from app.scheduler import build_executor
from app.time_provider import FakeTimeProvider, SystemTimeProvider, TimeProvider

SCENARIO_REPLAY = "replay"
SCENARIO_REFUND = "refund"
_SCENARIOS = (SCENARIO_REPLAY, SCENARIO_REFUND)


class DemoAuthorizationError(Exception):
    """No active demo session matches the given order id / token pair. A client
    must never be able to point a demo endpoint at an arbitrary order id — see
    the demo_transaction_isolation requirement in the build prompt."""


class DemoStateError(Exception):
    """A demo action was attempted out of order, e.g. dropping a webhook before
    the payment has actually captured."""


def _is_test_mode(settings: Settings) -> bool:
    # PayPal is always test mode here: Settings rejects any base URL but the sandbox.
    return (
        settings.gateway_mode in ("mock", "paypal")
        or (settings.gateway_mode == "razorpay" and settings.razorpay_key_id.startswith("rzp_test_"))
    )


def display_currency(settings: Settings) -> str:
    return settings.paypal_currency if settings.gateway_mode == "paypal" else "INR"


@asynccontextmanager
async def paypal_adapter(db: AsyncSession, settings: Settings):
    async def _no_delivery(payload):
        pass

    async with httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=30.0) as http:
        yield PayPalAdapter(
            db, http, settings.paypal_client_id, settings.paypal_client_secret, _no_delivery,
            currency=settings.paypal_currency,
        )


def _demo_can_execute(settings: Settings, amount_paise: int) -> bool:
    """The five conditions from safety_constraints under which a demo action may
    actually execute instead of DRY_RUN — independent of the deployment's global
    DRY_RUN default, which stays untouched for every non-demo transaction."""
    return _is_test_mode(settings) and amount_paise <= settings.demo_max_amount_paise


async def get_session(db: AsyncSession, order_id: str, token: str) -> DemoSession:
    demo_session = await db.get(DemoSession, token)
    if demo_session is None or demo_session.order_id != order_id:
        raise DemoAuthorizationError(f"no active demo session for order {order_id!r} with the given token")
    return demo_session


async def create_order(
    db: AsyncSession, settings: Settings, scenario: str, force_mock: bool = False, return_url: str | None = None
) -> tuple[DemoSession, str | None]:
    """Creates a demo order. In GATEWAY_MODE=razorpay, also creates a real
    Razorpay test-mode Payment Link (the same proven mechanism as
    scripts/razorpay_manual_checkout.py) and returns its URL for the evaluator
    to complete manually. force_mock always captures a scripted mock payment
    instead, regardless of GATEWAY_MODE — used by run-failure-scenario for
    evaluators who don't want to leave the dashboard."""
    if scenario not in _SCENARIOS:
        raise ValueError(f"unknown demo scenario {scenario!r}, expected one of {_SCENARIOS}")

    amount_paise = min(settings.demo_order_amount_paise, settings.demo_max_amount_paise)
    order_id = f"demo_{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(24)
    time_provider = SystemTimeProvider()

    merchant = MerchantService(db, time_provider)
    await merchant.create_order(order_id, amount_paise, currency=display_currency(settings) if not force_mock else "INR")

    demo_session = DemoSession(
        token=token,
        order_id=order_id,
        scenario=scenario,
        status="created",
        expires_at=time_provider.now() + timedelta(minutes=settings.demo_session_ttl_minutes),
    )
    db.add(demo_session)

    checkout_url: str | None = None
    if settings.gateway_mode == "razorpay" and not force_mock:
        client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
        link = client.payment_link.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "description": "Limbo live demo — Premium Plan",
                "reference_id": order_id,
                "notify": {"sms": False, "email": False},
            }
        )
        demo_session.gateway_reference = link["id"]
        checkout_url = link["short_url"]
    elif settings.gateway_mode == "paypal" and not force_mock:
        # A real PayPal sandbox order. The evaluator approves it with a sandbox
        # buyer account; PayPal then redirects back to the demo page, which
        # calls drop_webhook to capture it (ADR-018 pattern, now for PayPal).
        async with paypal_adapter(db, settings) as gateway:
            paypal_order_id, checkout_url = await gateway.create_checkout_order(
                order_id, amount_paise, return_url or "https://example.com/limbo-paypal-return"
            )
        demo_session.gateway_reference = paypal_order_id
    else:
        async def _no_delivery(payload):
            pass  # the demo always simulates a dropped webhook; see drop_webhook

        gateway = MockGatewayAdapter(db, _no_delivery, time_provider)
        await gateway.capture_payment(order_id, amount_paise, method="card")

    await db.commit()
    return demo_session, checkout_url


async def drop_webhook(db: AsyncSession, settings: Settings, demo_session: DemoSession) -> Payment:
    """Simulates the merchant never receiving the payment-captured webhook, using
    the existing ChaosEngine abstraction — the same WEBHOOK_DROPPED mechanism
    scripts/chaos_batch.py uses. For a real Razorpay payment there is no live
    webhook receiver at all (ADR-018), so this is also the first point the
    local ledger learns the human actually paid."""
    time_provider = SystemTimeProvider()
    payment = (
        await db.execute(select(Payment).where(Payment.order_id == demo_session.order_id))
    ).scalars().first()

    if payment is None and settings.gateway_mode == "razorpay":
        client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
        link = client.payment_link.fetch(demo_session.gateway_reference)
        captured = [p for p in (link.get("payments") or []) if p["status"] == "captured"]
        if not captured:
            raise DemoStateError(f"no captured payment yet for order {demo_session.order_id!r} — complete checkout first")
        payment_id = captured[-1]["payment_id"]
        real_payment = client.payment.fetch(payment_id)
        payment = Payment(
            id=payment_id,
            order_id=demo_session.order_id,
            amount_paise=real_payment["amount"],
            gateway_status=PaymentGatewayStatus.CAPTURED,
            method=real_payment["method"],
            captured_at=datetime.fromtimestamp(real_payment["created_at"], tz=timezone.utc),
        )
        db.add(payment)
        await db.flush()

    if payment is None and settings.gateway_mode == "paypal" and demo_session.gateway_reference:
        async with paypal_adapter(db, settings) as gateway:
            try:
                capture_id = await gateway.capture_order(demo_session.gateway_reference)
            except httpx.HTTPStatusError as exc:
                raise DemoStateError(
                    f"PayPal could not capture order {demo_session.gateway_reference!r} "
                    f"(HTTP {exc.response.status_code}) — approve it with the sandbox buyer account first"
                ) from exc
            capture = await gateway.fetch_existing_capture(capture_id)
        payment = Payment(
            id=capture_id,
            order_id=demo_session.order_id,
            amount_paise=to_minor_units(capture["amount"]["value"]),
            gateway_status=PaymentGatewayStatus.CAPTURED,
            method="paypal",
            captured_at=datetime.fromisoformat(capture["create_time"].replace("Z", "+00:00")),
        )
        db.add(payment)
        await db.flush()
        await audit.record(
            db,
            stage="gateway",
            event="paypal.capture.completed",
            order_id=demo_session.order_id,
            payment_id=capture_id,
            inputs={"paypal_order_id": demo_session.gateway_reference, "status": capture["status"]},
            reasoning=f"Real PayPal sandbox capture {capture_id} for {capture['amount']['value']} {capture['amount']['currency_code']}.",
        )

    if payment is None:
        raise DemoStateError(f"order {demo_session.order_id!r} has no captured payment to drop a webhook for")

    merchant = MerchantService(db, time_provider)
    chaos = ChaosEngine(db, time_provider)
    entity = PaymentEntity(
        id=payment.id,
        order_id=demo_session.order_id,
        amount=payment.amount_paise,
        currency=display_currency(settings) if payment.method == "paypal" else "INR",
        status=PaymentGatewayStatus.CAPTURED.value,
        method=payment.method,
    )
    payload = WebhookPayload(event=WebhookEventType.PAYMENT_CAPTURED, payload={"payment": {"entity": entity}})
    await chaos.deliver(payload, merchant, injected=FailureClass.WEBHOOK_DROPPED)

    demo_session.status = "webhook_dropped"
    await db.commit()
    return payment


async def drive_scenario(db: AsyncSession, settings: Settings, demo_session: DemoSession, payment: Payment) -> None:
    """Drives one accelerated pipeline pass for this order only, reusing the
    production ReconciliationEngine, DiagnosisEngine, and PolicyEngine
    unmodified. Scenario A (replay) uses an ordinary merchant cancellation while
    still inside the fulfillable window, exactly as scripts/verify_phase4.py
    exercises REPLAY_WEBHOOK. Scenario B (refund) uses a FakeTimeProvider
    fast-forwarded past the window, exactly as the Phase 7 eval harness fast-
    forwards SLA windows — scoped to this order only, so no other order's
    classification is affected by the advanced clock."""
    order_id = demo_session.order_id
    fulfillable_window = timedelta(minutes=settings.order_fulfillable_window_minutes)
    refund_sla = timedelta(hours=settings.refund_settlement_sla_hours)

    time_provider: TimeProvider
    if demo_session.scenario == SCENARIO_REPLAY:
        time_provider = SystemTimeProvider()
        merchant = MerchantService(db, time_provider)
        await merchant.cancel_order(order_id)
    else:
        time_provider = FakeTimeProvider(start=payment.captured_at)
        time_provider.advance(fulfillable_window + timedelta(seconds=settings.demo_fulfillable_window_seconds))
        merchant = MerchantService(db, time_provider)
        await merchant.expire_unconfirmed_orders(cutoff=time_provider.now(), order_ids=[order_id])
    await db.commit()

    recon = ReconciliationEngine(db, fulfillable_window, refund_sla, time_provider)
    await recon.run(order_ids=[order_id])
    await db.commit()

    latest = await latest_results_by_order(db, [order_id])
    result = latest.get(order_id)
    if result is None:
        return

    async with AsyncExitStack() as stack:
        paypal_get = None
        if settings.gateway_mode == "paypal" and payment.method == "paypal":
            paypal_get = (await stack.enter_async_context(paypal_adapter(db, settings))).get
        diagnosis = DiagnosisEngine(
            db,
            fulfillable_window_minutes=settings.order_fulfillable_window_minutes,
            refund_sla_hours=settings.refund_settlement_sla_hours,
            gemini_api_key=settings.gemini_api_key,
            time_provider=time_provider,
            paypal_get=paypal_get,
            commit_tool_calls=True,
        )
        await diagnosis.diagnose(order_id)
    await db.commit()

    async def webhook_sink(payload):
        pass  # completion webhooks from policy-triggered gateway calls are not re-delivered to ourselves

    order = await db.get(Order, order_id)
    async with AsyncExitStack() as stack:
        if payment.method == "paypal":
            gateway = await stack.enter_async_context(paypal_adapter(db, settings))
        elif settings.gateway_mode == "paypal":
            gateway = MockGatewayAdapter(db, webhook_sink, time_provider)  # scripted mock payment, never sent to PayPal
        else:
            gateway = build_gateway_adapter(settings, db, webhook_sink, time_provider)
        await _decide_and_act(db, settings, order_id, order, payment, result, merchant, gateway, time_provider, fulfillable_window)
    await db.commit()

    demo_session.status = "complete"
    await db.commit()


async def _decide_and_act(db, settings, order_id, order, payment, result, merchant, gateway, time_provider, fulfillable_window):
    within_window = time_provider.now() - payment.captured_at < fulfillable_window

    policy = PolicyEngine(
        db,
        max_auto_amount_paise=settings.max_auto_amount_paise,
        max_retry_attempts=settings.max_retry_attempts,
        cooldown_base=timedelta(seconds=1),
        circuit_breaker=CircuitBreaker(threshold=settings.circuit_breaker_false_intervention_rate),
        dry_run=not _demo_can_execute(settings, order.amount_paise),
        time_provider=time_provider,
    )
    action = await policy.decide(order_id, result.state, order.amount_paise, within_window)
    if action is not None:
        executor = await build_executor(action, order_id, payment, order.amount_paise, merchant, gateway, db)
        await policy.act(order_id, payment.id, action, order.amount_paise, executor)


async def cleanup_expired(db: AsyncSession) -> int:
    """Deletes domain rows for demo transactions past their TTL — order,
    payment, refund/webhook history, and recon/diagnosis/policy/chaos records —
    so repeated public demonstrations don't grow the database indefinitely.
    Never touches audit_log: those rows are the append-only record the audit
    trail exists to prove, and are excluded from demo cleanup entirely."""
    now = SystemTimeProvider().now()
    expired = (await db.execute(select(DemoSession).where(DemoSession.expires_at < now))).scalars().all()
    if not expired:
        return 0

    order_ids = [d.order_id for d in expired]
    payment_ids = list(
        (await db.execute(select(Payment.id).where(Payment.order_id.in_(order_ids)))).scalars().all()
    )

    await db.execute(delete(PolicyAction).where(PolicyAction.order_id.in_(order_ids)))
    await db.execute(delete(ReconciliationResult).where(ReconciliationResult.order_id.in_(order_ids)))
    await db.execute(delete(DiagnosisRecord).where(DiagnosisRecord.order_id.in_(order_ids)))
    await db.execute(delete(ChaosInjection).where(ChaosInjection.order_id.in_(order_ids)))
    if payment_ids:
        await db.execute(delete(WebhookEvent).where(WebhookEvent.payment_id.in_(payment_ids)))
        await db.execute(delete(Refund).where(Refund.payment_id.in_(payment_ids)))
    await db.execute(delete(Payment).where(Payment.order_id.in_(order_ids)))
    await db.execute(delete(Order).where(Order.id.in_(order_ids)))
    await db.execute(delete(DemoSession).where(DemoSession.token.in_([d.token for d in expired])))
    await db.commit()
    return len(expired)
