"""Phase 5 verification: run the Phase 2/3 chaos batch, diagnose every
transaction from observable evidence only, and report diagnosis accuracy
against the chaos injection ground truth.

The diagnosis module itself never reads chaos_injections (see docs/decisions.md
ADR-007) — only this verification script does, and only to score the results
after diagnosis has already run.

If GEMINI_API_KEY is unset, every case falls back to the deterministic
classifier and this is reported explicitly rather than silently.

Run with Postgres up (docker-compose up postgres) and DATABASE_URL pointed at it:
    python -m scripts.verify_phase5
"""

import asyncio
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from app.chaos.enums import FailureClass
from app.config import settings
from app.diagnosis.enums import RootCauseClass
from app.diagnosis.service import DiagnosisEngine
from app.models.base import Base, async_session, engine
from app.models.chaos_injection import ChaosInjection
from app.time_provider import FakeTimeProvider
import app.models  # noqa: F401
from scripts.chaos_batch import run_chaos_batch

TRANSACTION_COUNT = 50
INJECTION_RATE = 0.30

# The chaos class actually injected, mapped to the root cause a correct diagnosis
# from observable evidence should reach.
EXPECTED_ROOT_CAUSE = {
    None: RootCauseClass.HEALTHY,
    FailureClass.WEBHOOK_DROPPED: RootCauseClass.WEBHOOK_DROPPED,
    FailureClass.WEBHOOK_MERCHANT_500: RootCauseClass.WEBHOOK_MERCHANT_500,
    FailureClass.WEBHOOK_DELAYED: RootCauseClass.WEBHOOK_DELAYED,
    FailureClass.WEBHOOK_OUT_OF_ORDER: RootCauseClass.WEBHOOK_OUT_OF_ORDER,
    FailureClass.WEBHOOK_DUPLICATED: RootCauseClass.WEBHOOK_DUPLICATED,
    FailureClass.REFUND_STUCK: RootCauseClass.REFUND_STUCK,
}


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    time_provider = FakeTimeProvider()

    async with async_session() as session:
        transactions = await run_chaos_batch(session, time_provider, TRANSACTION_COUNT, INJECTION_RATE)
        await session.commit()

        # Cross the refund SLA too, so REFUND_STUCK evidence is actually present
        # (mirrors ADR-013's reasoning from Phase 3).
        time_provider.advance(timedelta(hours=settings.refund_settlement_sla_hours + 1))

        diagnosis = DiagnosisEngine(
            session,
            fulfillable_window_minutes=settings.order_fulfillable_window_minutes,
            refund_sla_hours=settings.refund_settlement_sla_hours,
            gemini_api_key=settings.gemini_api_key,
            time_provider=time_provider,
        )

        outcomes = []
        for order_id, injected in transactions:
            result, source = await diagnosis.diagnose(order_id)
            outcomes.append((order_id, injected, result, source))
        await session.commit()

        order_ids = [order_id for order_id, _ in transactions]
        ground_truth = {
            ci.order_id: ci.failure_class
            for ci in (await session.execute(select(ChaosInjection).where(ChaosInjection.order_id.in_(order_ids)))).scalars()
        }

    mismatches = []
    sources_used = Counter()
    for order_id, injected, result, source in outcomes:
        sources_used[source] += 1
        expected = EXPECTED_ROOT_CAUSE[injected]
        if result.root_cause_class != expected:
            mismatches.append(f"{order_id} (injected={injected}): expected {expected.value}, got {result.root_cause_class.value} [{source}]")

    total = len(outcomes)
    correct = total - len(mismatches)
    accuracy = correct / total

    print(f"Diagnosis accuracy vs chaos ground truth: {correct}/{total} = {accuracy:.1%}")
    print(f"Sources used: {dict(sources_used)}")
    if settings.gemini_api_key:
        print("GEMINI_API_KEY is set - LLM path was exercised.")
    else:
        print("GEMINI_API_KEY is not set - every case used the deterministic fallback. LLM path unexercised.")

    if mismatches:
        print(f"\n{len(mismatches)} mismatch(es):")
        for m in mismatches:
            print(f"  - {m}")

    if accuracy < 1.0 and not settings.gemini_api_key:
        # The deterministic fallback is a rule-based mirror of the chaos engine's
        # own construction, so on this synthetic batch it should be exact. Any
        # miss here is a real bug, not "LLM noise" — fail loud.
        raise SystemExit(1)

    print("\nPASS")


if __name__ == "__main__":
    asyncio.run(main())
