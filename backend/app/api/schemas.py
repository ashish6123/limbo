from datetime import datetime

from pydantic import BaseModel


class SummaryResponse(BaseModel):
    money_at_risk_paise: int
    revenue_recovered_paise: int
    loss_prevented_paise: int
    baseline_resolution_days: int
    false_intervention_rate: float
    circuit_breaker_threshold: float
    executed_action_count: int
    currency: str = "INR"
    gateway_mode: str = "mock"
    ai_mode: str = "deterministic"


class LimboQueueItem(BaseModel):
    order_id: str
    payment_id: str | None
    state: str
    amount_paise: int
    currency: str = "INR"
    classified_at: datetime


class ExceptionItem(BaseModel):
    order_id: str
    payment_id: str | None
    action_type: str
    status: str
    reasoning: str | None
    created_at: datetime


class AuditLogItem(BaseModel):
    stage: str
    event: str
    order_id: str | None
    payment_id: str | None
    reasoning: str | None
    created_at: datetime
