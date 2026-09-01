from datetime import timedelta

import pytest

from app.policy.circuit_breaker import CircuitBreaker
from app.policy.engine import PolicyEngine
from app.policy.enums import ActionStatus, ActionType
from app.recon.enums import TransactionState
from app.time_provider import FakeTimeProvider

MAX_AUTO_AMOUNT_PAISE = 500_000
MAX_RETRY_ATTEMPTS = 3
COOLDOWN_BASE = timedelta(seconds=1)


def make_engine(session, dry_run: bool, time_provider: FakeTimeProvider, circuit_breaker: CircuitBreaker | None = None) -> PolicyEngine:
    return PolicyEngine(
        session,
        max_auto_amount_paise=MAX_AUTO_AMOUNT_PAISE,
        max_retry_attempts=MAX_RETRY_ATTEMPTS,
        cooldown_base=COOLDOWN_BASE,
        circuit_breaker=circuit_breaker or CircuitBreaker(threshold=0.02),
        dry_run=dry_run,
        time_provider=time_provider,
    )


@pytest.mark.asyncio
async def test_double_run_does_not_double_execute(session):
    time_provider = FakeTimeProvider()
    engine = make_engine(session, dry_run=False, time_provider=time_provider)
    call_count = 0

    async def executor():
        nonlocal call_count
        call_count += 1

    first = await engine.act("order_idem_1", "pay_1", ActionType.AUTO_REFUND, 1000, executor)
    second = await engine.act("order_idem_1", "pay_1", ActionType.AUTO_REFUND, 1000, executor)

    assert first == ActionStatus.EXECUTED
    assert second == ActionStatus.SKIPPED_IDEMPOTENT
    assert call_count == 1


@pytest.mark.asyncio
async def test_dry_run_never_calls_executor(session):
    time_provider = FakeTimeProvider()
    engine = make_engine(session, dry_run=True, time_provider=time_provider)
    call_count = 0

    async def executor():
        nonlocal call_count
        call_count += 1

    status = await engine.act("order_dry_1", "pay_1", ActionType.AUTO_REFUND, 1000, executor)

    assert status == ActionStatus.DRY_RUN
    assert call_count == 0


@pytest.mark.asyncio
async def test_tripped_circuit_breaker_escalates_without_executing(session):
    time_provider = FakeTimeProvider()
    breaker = CircuitBreaker(threshold=0.02, min_sample=1)
    breaker.record(was_false_intervention=True)
    assert breaker.tripped is True

    engine = make_engine(session, dry_run=False, time_provider=time_provider, circuit_breaker=breaker)
    call_count = 0

    async def executor():
        nonlocal call_count
        call_count += 1

    status = await engine.act("order_breaker_1", "pay_1", ActionType.AUTO_REFUND, 1000, executor)

    assert status == ActionStatus.ESCALATED
    assert call_count == 0


@pytest.mark.asyncio
async def test_decide_escalates_after_max_retry_attempts(session):
    time_provider = FakeTimeProvider()
    engine = make_engine(session, dry_run=False, time_provider=time_provider)

    async def failing_executor():
        raise RuntimeError("gateway unavailable")

    for attempt in range(MAX_RETRY_ATTEMPTS):
        await engine.act("order_retry_1", "pay_1", ActionType.RETRIGGER_REFUND, 1000, failing_executor)
        time_provider.advance(timedelta(hours=1))

    action = await engine.decide(
        "order_retry_1", TransactionState.REFUND_STUCK, amount_paise=1000, within_fulfillable_window=True
    )

    assert action == ActionType.ESCALATE_HUMAN


@pytest.mark.asyncio
async def test_decide_below_retry_limit_keeps_retriggering(session):
    time_provider = FakeTimeProvider()
    engine = make_engine(session, dry_run=False, time_provider=time_provider)

    async def failing_executor():
        raise RuntimeError("gateway unavailable")

    await engine.act("order_retry_2", "pay_1", ActionType.RETRIGGER_REFUND, 1000, failing_executor)
    time_provider.advance(timedelta(hours=1))

    action = await engine.decide(
        "order_retry_2", TransactionState.REFUND_STUCK, amount_paise=1000, within_fulfillable_window=True
    )

    assert action == ActionType.RETRIGGER_REFUND


@pytest.mark.asyncio
async def test_retry_within_cooldown_is_deferred(session):
    time_provider = FakeTimeProvider()
    engine = make_engine(session, dry_run=False, time_provider=time_provider)

    async def failing_executor():
        raise RuntimeError("gateway unavailable")

    await engine.act("order_cooldown_1", "pay_1", ActionType.RETRIGGER_REFUND, 1000, failing_executor)
    # No time advance: still within the cooldown window for attempt 2.
    status = await engine.act("order_cooldown_1", "pay_1", ActionType.RETRIGGER_REFUND, 1000, failing_executor)

    assert status == ActionStatus.DEFERRED_COOLDOWN
