import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, require_dashboard_key
from app.api.schemas import AuditLogItem, ExceptionItem, LimboQueueItem, SummaryResponse
from app.config import settings
from app.models.audit_log import AuditLog
from app.models.order import Order
from app.models.policy_action import PolicyAction
from app.policy.enums import ActionStatus, ActionType
from app.recon.enums import TransactionState
from app.recon.service import latest_results_by_order

router = APIRouter(prefix="/api", dependencies=[Depends(require_dashboard_key)])

_EVAL_REPORT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "evals" / "results" / "report.json"

_ACTIONABLE_STATES = (
    TransactionState.LIMBO_PAID_CANCELLED,
    TransactionState.REFUND_STUCK,
    TransactionState.DUPLICATE_PAYMENT,
    TransactionState.INVERSE_LEAK,
)
_REVENUE_ACTIONS = (ActionType.REPLAY_WEBHOOK,)
_LOSS_PREVENTED_ACTIONS = (ActionType.AUTO_REFUND, ActionType.RETRIGGER_REFUND, ActionType.DEDUP_REFUND)
_RESOLVING_ACTIONS = _REVENUE_ACTIONS + _LOSS_PREVENTED_ACTIONS


async def _resolved_order_ids(session: AsyncSession) -> set[str]:
    """Orders with an already-executed resolving action. Reconciliation runs on
    an interval (ADR-006) and is not re-triggered per action, so an order's
    latest recon snapshot can still show an actionable state after it has
    already been resolved — this is what the limbo queue / money-at-risk
    figures must exclude to avoid double-counting resolved cases as at-risk."""
    rows = (
        await session.execute(
            select(PolicyAction.order_id).where(
                PolicyAction.status == ActionStatus.EXECUTED, PolicyAction.action_type.in_(_RESOLVING_ACTIONS)
            )
        )
    ).scalars().all()
    return set(rows)


@router.get("/summary", response_model=SummaryResponse)
async def get_summary(session: AsyncSession = Depends(get_session)) -> SummaryResponse:
    latest = await latest_results_by_order(session)
    resolved = await _resolved_order_ids(session)
    limbo_order_ids = [
        order_id for order_id, result in latest.items() if result.state in _ACTIONABLE_STATES and order_id not in resolved
    ]

    orders_by_id = {}
    if limbo_order_ids:
        orders_by_id = {o.id: o for o in (await session.execute(select(Order).where(Order.id.in_(limbo_order_ids)))).scalars()}

    executed_actions = (
        await session.execute(select(PolicyAction).where(PolicyAction.status == ActionStatus.EXECUTED))
    ).scalars().all()

    money_at_risk_paise = sum(orders_by_id[oid].amount_paise for oid in limbo_order_ids if oid in orders_by_id)
    revenue_recovered_paise = sum(a.amount_paise for a in executed_actions if a.action_type in _REVENUE_ACTIONS)
    loss_prevented_paise = sum(a.amount_paise for a in executed_actions if a.action_type in _LOSS_PREVENTED_ACTIONS)

    false_intervention_rate = 0.0  # no ground truth available outside eval mode; see docs/decisions.md ADR-008

    # No live agent_mttr figure here: verification scripts and eval runs share
    # this DB and write PolicyAction/Order timestamps via a FakeTimeProvider
    # advanced by hours or days, so an average against real Order.created_at
    # values is not a real latency measurement — it was caught here reading
    # ~73h, exactly REFUND_SETTLEMENT_SLA_HOURS + 1h, a simulated clock jump,
    # not agent speed. See docs/decisions.md ADR-025. The eval report's
    # mttr.agent_resolution is honest because it stays within one single-clock run.
    return SummaryResponse(
        money_at_risk_paise=money_at_risk_paise,
        revenue_recovered_paise=revenue_recovered_paise,
        loss_prevented_paise=loss_prevented_paise,
        baseline_resolution_days=settings.baseline_resolution_days,
        false_intervention_rate=false_intervention_rate,
        circuit_breaker_threshold=settings.circuit_breaker_false_intervention_rate,
        executed_action_count=len(executed_actions),
    )


@router.get("/limbo-queue", response_model=list[LimboQueueItem])
async def get_limbo_queue(session: AsyncSession = Depends(get_session)) -> list[LimboQueueItem]:
    latest = await latest_results_by_order(session)
    resolved = await _resolved_order_ids(session)
    limbo_results = [r for r in latest.values() if r.state in _ACTIONABLE_STATES and r.order_id not in resolved]

    order_ids = [r.order_id for r in limbo_results]
    orders_by_id = {}
    if order_ids:
        orders_by_id = {o.id: o for o in (await session.execute(select(Order).where(Order.id.in_(order_ids)))).scalars()}

    return [
        LimboQueueItem(
            order_id=r.order_id,
            payment_id=r.payment_id,
            state=r.state.value,
            amount_paise=orders_by_id[r.order_id].amount_paise if r.order_id in orders_by_id else 0,
            classified_at=r.classified_at,
        )
        for r in sorted(limbo_results, key=lambda r: r.classified_at, reverse=True)
    ]


@router.get("/exceptions", response_model=list[ExceptionItem])
async def get_exceptions(session: AsyncSession = Depends(get_session)) -> list[ExceptionItem]:
    rows = (
        await session.execute(
            select(PolicyAction)
            .where(PolicyAction.status == ActionStatus.ESCALATED)
            .order_by(PolicyAction.created_at.desc())
        )
    ).scalars().all()
    return [
        ExceptionItem(
            order_id=r.order_id,
            payment_id=r.payment_id,
            action_type=r.action_type.value,
            status=r.status.value,
            reasoning=r.reasoning,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/audit-log", response_model=list[AuditLogItem])
async def get_audit_log(limit: int = Query(default=100, le=1000), session: AsyncSession = Depends(get_session)) -> list[AuditLogItem]:
    rows = (
        await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    ).scalars().all()
    return [
        AuditLogItem(
            stage=r.stage,
            event=r.event,
            order_id=r.order_id,
            payment_id=r.payment_id,
            reasoning=r.reasoning,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/eval-report")
async def get_eval_report() -> dict:
    if not _EVAL_REPORT_PATH.exists():
        return {"available": False}
    return {"available": True, "report": json.loads(_EVAL_REPORT_PATH.read_text())}
