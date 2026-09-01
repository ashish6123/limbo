from datetime import timedelta
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.models.policy_action import PolicyAction
from app.policy.circuit_breaker import CircuitBreaker
from app.policy.enums import ActionStatus, ActionType
from app.policy.selector import apply_guardrails, base_action_for_state
from app.recon.enums import TransactionState
from app.time_provider import SystemTimeProvider, TimeProvider

Executor = Callable[[], Awaitable[None]]


class PolicyEngine:
    """Guardrail-enforced action execution: idempotency, amount ceiling, retry
    limits with cooldown, the circuit breaker, and DRY_RUN. Every decision and
    action is written to the audit log."""

    def __init__(
        self,
        session: AsyncSession,
        max_auto_amount_paise: int,
        max_retry_attempts: int,
        cooldown_base: timedelta,
        circuit_breaker: CircuitBreaker,
        dry_run: bool = True,
        time_provider: TimeProvider = SystemTimeProvider(),
    ):
        self._session = session
        self._max_auto_amount_paise = max_auto_amount_paise
        self._max_retry_attempts = max_retry_attempts
        self._cooldown_base = cooldown_base
        self._circuit_breaker = circuit_breaker
        self._dry_run = dry_run
        self._time = time_provider

    async def _prior_attempts(self, idempotency_key: str) -> list[PolicyAction]:
        rows = (
            await self._session.execute(
                select(PolicyAction)
                .where(PolicyAction.idempotency_key == idempotency_key)
                .order_by(PolicyAction.attempt_number)
            )
        ).scalars().all()
        return list(rows)

    async def decide(
        self,
        order_id: str,
        state: TransactionState,
        amount_paise: int,
        within_fulfillable_window: bool,
    ) -> ActionType | None:
        base = base_action_for_state(state, within_fulfillable_window)
        if base is None:
            return None

        attempts = await self._prior_attempts(f"{order_id}:{base.value}")
        action = apply_guardrails(base, amount_paise, len(attempts), self._max_auto_amount_paise, self._max_retry_attempts)

        await audit.record(
            self._session,
            stage="decision",
            event=f"selected {action.value if action else 'no_action'}",
            order_id=order_id,
            inputs={"state": state.value, "amount_paise": amount_paise, "prior_attempts": len(attempts)},
            created_at=self._time.now(),
        )
        return action

    async def act(
        self,
        order_id: str,
        payment_id: str | None,
        action_type: ActionType,
        amount_paise: int,
        executor: Executor,
        reasoning: str | None = None,
    ) -> ActionStatus:
        idempotency_key = f"{order_id}:{action_type.value}"
        prior = await self._prior_attempts(idempotency_key)

        if any(a.status in (ActionStatus.EXECUTED, ActionStatus.DRY_RUN) for a in prior):
            await audit.record(
                self._session,
                stage="action",
                event=ActionStatus.SKIPPED_IDEMPOTENT.value,
                order_id=order_id,
                payment_id=payment_id,
                idempotency_key=idempotency_key,
                reasoning="already executed for this idempotency key",
                created_at=self._time.now(),
            )
            return ActionStatus.SKIPPED_IDEMPOTENT

        attempt_number = len(prior) + 1
        now = self._time.now()

        if prior:
            cooldown = self._cooldown_base * (2 ** (attempt_number - 2))
            if now - prior[-1].created_at < cooldown:
                return ActionStatus.DEFERRED_COOLDOWN

        if self._circuit_breaker.tripped:
            status = ActionStatus.ESCALATED
            error = "circuit breaker tripped: false-intervention rate exceeded threshold"
        elif self._dry_run:
            status = ActionStatus.DRY_RUN
            error = None
        else:
            try:
                await executor()
                status = ActionStatus.EXECUTED
                error = None
            except Exception as exc:
                status = ActionStatus.FAILED
                error = str(exc)

        row = PolicyAction(
            order_id=order_id,
            payment_id=payment_id,
            action_type=action_type,
            idempotency_key=idempotency_key,
            attempt_number=attempt_number,
            status=status,
            amount_paise=amount_paise,
            reasoning=error,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()

        await audit.record(
            self._session,
            stage="outcome",
            event=status.value,
            order_id=order_id,
            payment_id=payment_id,
            idempotency_key=idempotency_key,
            inputs={"action_type": action_type.value, "amount_paise": amount_paise, "attempt_number": attempt_number},
            reasoning=reasoning or error,
            created_at=now,
        )
        return status
