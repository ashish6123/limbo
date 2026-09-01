from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.demo import service as demo_service
from app.models.audit_log import AuditLog
from app.models.policy_action import PolicyAction
from app.policy.enums import ActionStatus, ActionType
from sqlalchemy import select


def make_settings(**overrides) -> Settings:
    defaults = dict(
        database_url="postgresql+asyncpg://unused/unused",
        gateway_mode="mock",
        dry_run=True,
        order_fulfillable_window_minutes=30,
        refund_settlement_sla_hours=72,
        demo_order_amount_paise=100_000,
        demo_max_amount_paise=100_000,
        demo_fulfillable_window_seconds=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_replay_scenario_recovers_via_replay_webhook(session):
    settings = make_settings()
    demo_session, checkout_url = await demo_service.create_order(session, settings, demo_service.SCENARIO_REPLAY)
    assert checkout_url is None

    payment = await demo_service.drop_webhook(session, settings, demo_session)
    await demo_service.drive_scenario(session, settings, demo_session, payment)

    action = (
        await session.execute(select(PolicyAction).where(PolicyAction.order_id == demo_session.order_id))
    ).scalars().first()
    assert action is not None
    assert action.action_type == ActionType.REPLAY_WEBHOOK
    assert action.status == ActionStatus.EXECUTED

    audit_rows = (
        await session.execute(select(AuditLog).where(AuditLog.order_id == demo_session.order_id))
    ).scalars().all()
    assert any(row.event == "LIMBO_PAID_CANCELLED" for row in audit_rows)
    assert any(row.event == ActionStatus.EXECUTED.value for row in audit_rows)


@pytest.mark.asyncio
async def test_refund_scenario_recovers_via_auto_refund(session):
    settings = make_settings()
    demo_session, _ = await demo_service.create_order(session, settings, demo_service.SCENARIO_REFUND)

    payment = await demo_service.drop_webhook(session, settings, demo_session)
    await demo_service.drive_scenario(session, settings, demo_session, payment)

    action = (
        await session.execute(select(PolicyAction).where(PolicyAction.order_id == demo_session.order_id))
    ).scalars().first()
    assert action is not None
    assert action.action_type == ActionType.AUTO_REFUND
    assert action.status == ActionStatus.EXECUTED


@pytest.mark.asyncio
async def test_wrong_token_is_rejected(session):
    settings = make_settings()
    demo_session, _ = await demo_service.create_order(session, settings, demo_service.SCENARIO_REPLAY)

    with pytest.raises(demo_service.DemoAuthorizationError):
        await demo_service.get_session(session, demo_session.order_id, "not-the-real-token")


@pytest.mark.asyncio
async def test_client_cannot_point_webhook_drop_at_arbitrary_order(session):
    settings = make_settings()
    demo_session, _ = await demo_service.create_order(session, settings, demo_service.SCENARIO_REPLAY)

    with pytest.raises(demo_service.DemoAuthorizationError):
        await demo_service.get_session(session, "order_not_mine", demo_session.token)


@pytest.mark.asyncio
async def test_demo_execution_stays_dry_run_above_max_amount(session):
    # create_order already clamps the order amount to demo_max_amount_paise, so
    # exercise the defense-in-depth check in drive_scenario directly: simulate
    # an order whose amount ended up above the ceiling by the time it's acted on.
    settings = make_settings()
    demo_session, _ = await demo_service.create_order(session, settings, demo_service.SCENARIO_REPLAY)
    payment = await demo_service.drop_webhook(session, settings, demo_session)

    from app.models.order import Order

    order = await session.get(Order, demo_session.order_id)
    order.amount_paise = settings.demo_max_amount_paise + 1
    await session.commit()

    await demo_service.drive_scenario(session, settings, demo_session, payment)

    action = (
        await session.execute(select(PolicyAction).where(PolicyAction.order_id == demo_session.order_id))
    ).scalars().first()
    assert action.status == ActionStatus.DRY_RUN


@pytest.mark.asyncio
async def test_cleanup_expired_removes_only_expired_sessions(session):
    from datetime import timedelta

    settings = make_settings()
    keep, _ = await demo_service.create_order(session, settings, demo_service.SCENARIO_REPLAY)
    expire, _ = await demo_service.create_order(session, settings, demo_service.SCENARIO_REPLAY)
    expire.expires_at = expire.created_at - timedelta(minutes=1)
    await session.commit()

    removed = await demo_service.cleanup_expired(session)

    assert removed >= 1
    from app.models.demo_session import DemoSession

    remaining_tokens = {d.token for d in (await session.execute(select(DemoSession))).scalars().all()}
    assert keep.token in remaining_tokens
    assert expire.token not in remaining_tokens


@pytest.mark.asyncio
async def test_drop_webhook_extracts_real_payment_id_from_payment_link(session):
    # Regression test: a live checkout against real Razorpay test mode showed
    # the payment link's "payments" entries key the payment id as "payment_id",
    # not "id" as originally (wrongly) assumed — this pins the real shape down.
    settings = make_settings(gateway_mode="razorpay")

    import uuid

    fake_payment_id = f"pay_realshapeexample_{uuid.uuid4().hex[:10]}"
    fake_client = MagicMock()
    fake_client.payment_link.create.return_value = {"id": "plink_fake123", "short_url": "https://rzp.io/rzp/fake123"}
    fake_client.payment_link.fetch.return_value = {
        "id": "plink_fake123",
        "status": "paid",
        "payments": [{"payment_id": fake_payment_id, "status": "captured", "amount": 100000, "method": "card"}],
    }
    fake_client.payment.fetch.return_value = {
        "id": fake_payment_id,
        "amount": 100000,
        "method": "card",
        "created_at": int(datetime.now(timezone.utc).timestamp()),
    }

    with patch("app.demo.service.razorpay.Client", return_value=fake_client):
        demo_session, checkout_url = await demo_service.create_order(session, settings, demo_service.SCENARIO_REPLAY)
        assert checkout_url == "https://rzp.io/rzp/fake123"

        payment = await demo_service.drop_webhook(session, settings, demo_session)

    assert payment.id == fake_payment_id
