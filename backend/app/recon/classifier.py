from datetime import datetime, timedelta

from app.models.enums import OrderState, PaymentGatewayStatus, RefundGatewayStatus
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.recon.enums import TransactionState

def classify(
    order: Order,
    payments: list[Payment],
    refunds: list[Refund],
    now: datetime,
    fulfillable_window: timedelta,
    refund_sla: timedelta,
) -> TransactionState | None:
    """Pure, deterministic six-state classification of one order against its
    gateway payment/refund records. Returns None when the order has no
    outstanding captured payment and was never fulfilled — there is nothing left
    to reconcile. A payment already REFUNDED does not count as outstanding: once
    a refund has actually gone through, the mismatch it existed to resolve is
    gone, and it should drop out of the recon result set entirely."""
    captured = [p for p in payments if p.gateway_status == PaymentGatewayStatus.CAPTURED]

    if len(captured) >= 2:
        return TransactionState.DUPLICATE_PAYMENT

    stuck_refunds = [
        r
        for r in refunds
        if r.gateway_status == RefundGatewayStatus.INITIATED
        and r.settled_at is None
        and now - r.initiated_at >= refund_sla
    ]
    if stuck_refunds:
        return TransactionState.REFUND_STUCK

    order_fulfilled = order.state in (OrderState.CONFIRMED, OrderState.FULFILLED)
    order_cancelled = order.state == OrderState.CANCELLED

    if captured:
        if order_fulfilled:
            return TransactionState.MATCHED_HEALTHY
        if order_cancelled:
            return TransactionState.LIMBO_PAID_CANCELLED
        if now - captured[0].captured_at < fulfillable_window:
            return TransactionState.PENDING_WINDOW
        return TransactionState.LIMBO_PAID_CANCELLED

    if order_fulfilled:
        return TransactionState.INVERSE_LEAK

    if order.state == OrderState.AWAITING_PAYMENT and now - order.created_at < fulfillable_window:
        return TransactionState.PENDING_WINDOW

    return None
