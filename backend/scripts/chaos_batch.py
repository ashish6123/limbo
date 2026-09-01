"""Shared batch-execution helper. run_scenario_batch drives an arbitrary list of
scenarios (order id, amount, method, injected failure class) through the chaos
engine; run_chaos_batch wraps it with the random assignment used by the phase
2-6 verification scripts. The eval harness (evals/runner.py) calls
run_scenario_batch directly with its own generated scenarios, so both paths
share one execution path rather than two near-identical copies."""

import random
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.chaos.engine import ChaosEngine
from app.chaos.enums import FailureClass
from app.config import settings
from app.gateway.mock import MockGatewayAdapter
from app.merchant.service import MerchantService
from app.time_provider import FakeTimeProvider


def build_class_assignments(transaction_count: int, injection_rate: float, seed: int) -> list[FailureClass | None]:
    injected_count = round(transaction_count * injection_rate)
    classes = list(FailureClass)
    assignments: list[FailureClass | None] = [classes[i % len(classes)] for i in range(injected_count)]
    assignments += [None] * (transaction_count - injected_count)
    random.Random(seed).shuffle(assignments)
    return assignments


async def run_scenario_batch(
    session: AsyncSession,
    time_provider: FakeTimeProvider,
    scenarios: list[dict],
) -> list[tuple[str, FailureClass | None]]:
    """Each scenario dict needs: id, amount_paise, method, injected_class (a
    FailureClass value string or None)."""
    merchant = MerchantService(session, time_provider)
    chaos = ChaosEngine(session, time_provider)

    transactions: list[tuple[str, FailureClass | None]] = []
    delayed_pending: list[tuple] = []

    for scenario in scenarios:
        order_id = f"order_{scenario['id']}"
        amount_paise = scenario["amount_paise"]
        injected = FailureClass(scenario["injected_class"]) if scenario["injected_class"] else None

        await merchant.create_order(order_id, amount_paise)

        async def sink(payload, injected=injected):
            if injected == FailureClass.WEBHOOK_DELAYED:
                delayed_pending.append((payload, injected))
                return
            await chaos.deliver(payload, merchant, injected)

        gateway = MockGatewayAdapter(session, webhook_sink=sink, time_provider=time_provider)
        payment_id = await gateway.capture_payment(order_id, amount_paise, method=scenario["method"])
        transactions.append((order_id, injected))

        if injected == FailureClass.REFUND_STUCK:
            await chaos.initiate_refund_stuck(payment_id, amount_paise)

    # Sweep: fast-forward past the fulfillable window, then run the merchant's own
    # timeout cancellation. This is what turns DROPPED/500/still-pending DELAYED
    # orders into the flagship LIMBO_PAID_CANCELLED footprint.
    time_provider.advance(timedelta(minutes=settings.order_fulfillable_window_minutes + 5))
    await merchant.expire_unconfirmed_orders(cutoff=time_provider.now())

    # Now deliver the delayed webhooks — late, against orders already cancelled.
    for payload, injected in delayed_pending:
        await chaos.deliver(payload, merchant, injected)

    return transactions


async def run_chaos_batch(
    session: AsyncSession,
    time_provider: FakeTimeProvider,
    transaction_count: int = 50,
    injection_rate: float = 0.30,
    amount_paise: int = 200_00,
    seed: int = 42,
) -> list[tuple[str, FailureClass | None]]:
    assignments = build_class_assignments(transaction_count, injection_rate, seed)
    scenarios = [
        {
            "id": uuid.uuid4().hex[:12],
            "amount_paise": amount_paise,
            "method": "upi",
            "injected_class": injected.value if injected else None,
        }
        for injected in assignments
    ]
    return await run_scenario_batch(session, time_provider, scenarios)
