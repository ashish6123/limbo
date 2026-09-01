from typing import Literal

from pydantic import BaseModel

from app.models.enums import WebhookEventType


class PaymentEntity(BaseModel):
    id: str
    order_id: str
    amount: int
    currency: str
    status: str
    method: str


class RefundEntity(BaseModel):
    id: str
    payment_id: str
    amount: int
    status: str


class WebhookPayload(BaseModel):
    """Mirrors Razorpay's webhook envelope: {event, payload: {payment: {entity}}}."""

    event: WebhookEventType
    payload: dict[Literal["payment", "refund"], dict[Literal["entity"], PaymentEntity | RefundEntity]]
