from datetime import datetime, timedelta, timezone

from app.models.enums import OrderState, PaymentGatewayStatus, RefundGatewayStatus
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.recon.classifier import classify
from app.recon.enums import TransactionState

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
WINDOW = timedelta(minutes=30)
REFUND_SLA = timedelta(hours=72)


def make_order(state: OrderState, created_at: datetime = NOW) -> Order:
    return Order(id="order_1", amount_paise=10000, currency="INR", state=state, created_at=created_at)


def make_payment(status: PaymentGatewayStatus, captured_at: datetime = NOW, payment_id: str = "pay_1") -> Payment:
    return Payment(
        id=payment_id, order_id="order_1", amount_paise=10000, gateway_status=status, method="upi", captured_at=captured_at
    )


def make_refund(status: RefundGatewayStatus, initiated_at: datetime, settled_at: datetime | None, payment_id: str = "pay_1") -> Refund:
    return Refund(
        id="rfnd_1", payment_id=payment_id, amount_paise=10000, gateway_status=status, initiated_at=initiated_at, settled_at=settled_at
    )


def test_captured_payment_and_confirmed_order_is_matched_healthy():
    order = make_order(OrderState.CONFIRMED)
    payment = make_payment(PaymentGatewayStatus.CAPTURED)

    result = classify(order, [payment], [], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.MATCHED_HEALTHY


def test_captured_payment_and_cancelled_order_is_limbo_paid_cancelled():
    order = make_order(OrderState.CANCELLED)
    payment = make_payment(PaymentGatewayStatus.CAPTURED)

    result = classify(order, [payment], [], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.LIMBO_PAID_CANCELLED


def test_captured_payment_stuck_awaiting_past_window_is_limbo_paid_cancelled():
    order = make_order(OrderState.AWAITING_PAYMENT, created_at=NOW - timedelta(hours=2))
    payment = make_payment(PaymentGatewayStatus.CAPTURED, captured_at=NOW - timedelta(hours=2))

    result = classify(order, [payment], [], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.LIMBO_PAID_CANCELLED


def test_no_payment_but_order_confirmed_is_inverse_leak():
    order = make_order(OrderState.CONFIRMED)

    result = classify(order, [], [], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.INVERSE_LEAK


def test_two_captured_payments_is_duplicate_payment():
    order = make_order(OrderState.CONFIRMED)
    payment_a = make_payment(PaymentGatewayStatus.CAPTURED, payment_id="pay_a")
    payment_b = make_payment(PaymentGatewayStatus.CAPTURED, payment_id="pay_b")

    result = classify(order, [payment_a, payment_b], [], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.DUPLICATE_PAYMENT


def test_refund_initiated_past_sla_with_no_settlement_is_refund_stuck():
    order = make_order(OrderState.CONFIRMED)
    payment = make_payment(PaymentGatewayStatus.CAPTURED)
    refund = make_refund(RefundGatewayStatus.INITIATED, initiated_at=NOW - timedelta(hours=100), settled_at=None)

    result = classify(order, [payment], [refund], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.REFUND_STUCK


def test_refund_initiated_within_sla_is_not_yet_stuck():
    order = make_order(OrderState.CONFIRMED)
    payment = make_payment(PaymentGatewayStatus.CAPTURED)
    refund = make_refund(RefundGatewayStatus.INITIATED, initiated_at=NOW - timedelta(hours=1), settled_at=None)

    result = classify(order, [payment], [refund], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.MATCHED_HEALTHY


def test_refund_already_settled_is_not_refund_stuck():
    order = make_order(OrderState.CONFIRMED)
    payment = make_payment(PaymentGatewayStatus.CAPTURED)
    refund = make_refund(RefundGatewayStatus.PROCESSED, initiated_at=NOW - timedelta(hours=100), settled_at=NOW - timedelta(hours=99))

    result = classify(order, [payment], [refund], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.MATCHED_HEALTHY


def test_captured_payment_awaiting_within_window_is_pending():
    order = make_order(OrderState.AWAITING_PAYMENT, created_at=NOW - timedelta(minutes=5))
    payment = make_payment(PaymentGatewayStatus.CAPTURED, captured_at=NOW - timedelta(minutes=5))

    result = classify(order, [payment], [], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.PENDING_WINDOW


def test_no_payment_awaiting_within_window_is_pending():
    order = make_order(OrderState.AWAITING_PAYMENT, created_at=NOW - timedelta(minutes=5))

    result = classify(order, [], [], NOW, WINDOW, REFUND_SLA)

    assert result == TransactionState.PENDING_WINDOW


def test_refunded_payment_with_cancelled_order_has_nothing_left_to_reconcile():
    order = make_order(OrderState.CANCELLED)
    payment = make_payment(PaymentGatewayStatus.REFUNDED)

    result = classify(order, [payment], [], NOW, WINDOW, REFUND_SLA)

    assert result is None


def test_no_payment_and_no_fulfillment_has_nothing_to_reconcile():
    order = make_order(OrderState.CANCELLED, created_at=NOW - timedelta(hours=2))

    result = classify(order, [], [], NOW, WINDOW, REFUND_SLA)

    assert result is None
