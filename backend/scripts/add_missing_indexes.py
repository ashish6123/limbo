"""One-off, idempotent index backfill for local dev databases created before
these indexes were added to the models. Base.metadata.create_all does not
retroactively add indexes to tables that already exist (only to newly created
ones), so a fresh Supabase deploy gets these for free — this script is only
needed against a pre-existing local DB.

Run:
    python -m scripts.add_missing_indexes
"""

import asyncio

from sqlalchemy import text

from app.models.base import engine

_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS ix_audit_log_order_id ON audit_log (order_id)",
    "CREATE INDEX IF NOT EXISTS ix_policy_actions_order_id ON policy_actions (order_id)",
    "CREATE INDEX IF NOT EXISTS ix_policy_actions_idempotency_key ON policy_actions (idempotency_key)",
    "CREATE INDEX IF NOT EXISTS ix_policy_actions_status ON policy_actions (status)",
    "CREATE INDEX IF NOT EXISTS ix_reconciliation_results_order_id ON reconciliation_results (order_id)",
    "CREATE INDEX IF NOT EXISTS ix_payments_order_id ON payments (order_id)",
    "CREATE INDEX IF NOT EXISTS ix_refunds_payment_id ON refunds (payment_id)",
    "CREATE INDEX IF NOT EXISTS ix_webhook_events_payment_id ON webhook_events (payment_id)",
    "CREATE INDEX IF NOT EXISTS ix_diagnosis_records_order_id ON diagnosis_records (order_id)",
    "CREATE INDEX IF NOT EXISTS ix_chaos_injections_order_id ON chaos_injections (order_id)",
    "CREATE INDEX IF NOT EXISTS ix_orders_created_at ON orders (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_demo_sessions_expires_at ON demo_sessions (expires_at)",
]


async def main() -> None:
    async with engine.begin() as conn:
        for statement in _STATEMENTS:
            await conn.execute(text(statement))
            print(f"ok: {statement}")


if __name__ == "__main__":
    asyncio.run(main())
