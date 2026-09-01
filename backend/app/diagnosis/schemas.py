from datetime import datetime

from pydantic import BaseModel, Field

from app.diagnosis.enums import RootCauseClass


class WebhookEventEvidence(BaseModel):
    event_type: str
    delivered: bool
    delivered_at: datetime | None
    merchant_response_code: int | None
    attempt_number: int


class RefundEvidence(BaseModel):
    gateway_status: str
    initiated_at: datetime
    settled_at: datetime | None


class EvidenceBundle(BaseModel):
    """Everything the diagnosis agent is allowed to see: observable webhook and
    payment/refund evidence only. Never includes chaos injection ground truth —
    see docs/decisions.md ADR-007."""

    order_id: str
    order_state: str
    payment_id: str | None
    payment_status: str | None
    payment_captured_at: datetime | None
    webhook_events: list[WebhookEventEvidence]
    refunds: list[RefundEvidence]
    now: datetime
    fulfillable_window_minutes: int
    refund_sla_hours: int


class DiagnosisResult(BaseModel):
    """JSON-constrained diagnosis output, produced identically by the LLM path
    (via structured outputs) and the deterministic fallback."""

    root_cause_class: RootCauseClass
    confidence: float = Field(ge=0.0, le=1.0)
    human_readable_narrative: str
    recommended_action: str
