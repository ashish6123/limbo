import enum


class OrderState(str, enum.Enum):
    CREATED = "CREATED"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    FULFILLED = "FULFILLED"


class PaymentGatewayStatus(str, enum.Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


class RefundGatewayStatus(str, enum.Enum):
    INITIATED = "initiated"
    PROCESSED = "processed"
    FAILED = "failed"


class WebhookEventType(str, enum.Enum):
    PAYMENT_CAPTURED = "payment.captured"
    PAYMENT_FAILED = "payment.failed"
    REFUND_PROCESSED = "refund.processed"
