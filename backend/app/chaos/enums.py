import enum


class FailureClass(str, enum.Enum):
    WEBHOOK_DROPPED = "webhook_dropped"
    WEBHOOK_DELAYED = "webhook_delayed"
    WEBHOOK_MERCHANT_500 = "webhook_merchant_500"
    WEBHOOK_OUT_OF_ORDER = "webhook_out_of_order"
    WEBHOOK_DUPLICATED = "webhook_duplicated"
    REFUND_STUCK = "refund_stuck"
