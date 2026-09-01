from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def record(
    session: AsyncSession,
    stage: str,
    event: str,
    order_id: str | None = None,
    payment_id: str | None = None,
    idempotency_key: str | None = None,
    inputs: dict | None = None,
    reasoning: str | None = None,
    created_at: datetime | None = None,
) -> AuditLog:
    """Append-only audit entry. Never update or delete a row once written."""
    entry = AuditLog(
        order_id=order_id,
        payment_id=payment_id,
        stage=stage,
        event=event,
        idempotency_key=idempotency_key,
        inputs=inputs or {},
        reasoning=reasoning,
        **({"created_at": created_at} if created_at is not None else {}),
    )
    session.add(entry)
    await session.flush()
    return entry


async def for_order(session: AsyncSession, order_id: str, after_id: int = 0) -> list[AuditLog]:
    """All audit entries for one order, oldest first. after_id lets a caller
    poll for only what's new since the last row it saw."""
    rows = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.order_id == order_id, AuditLog.id > after_id)
            .order_by(AuditLog.id)
        )
    ).scalars().all()
    return list(rows)
