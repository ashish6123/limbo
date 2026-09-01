from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChaosInjection(Base):
    """Ground truth of which failure class was deliberately injected into which
    transaction. Eval-only: the diagnosis module must never read this table, or
    diagnosis accuracy would be measured against an agent that saw the answer key.
    See docs/decisions.md ADR-007."""

    __tablename__ = "chaos_injections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_class: Mapped[str] = mapped_column(String, nullable=False)
    injected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
