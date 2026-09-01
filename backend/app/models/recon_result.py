from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.recon.enums import TransactionState


class ReconciliationResult(Base):
    __tablename__ = "reconciliation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[TransactionState] = mapped_column(Enum(TransactionState, native_enum=False), nullable=False)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
