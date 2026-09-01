from datetime import datetime, timedelta, timezone

from app.diagnosis.deterministic import diagnose_deterministic
from app.diagnosis.enums import RootCauseClass
from app.diagnosis.schemas import EvidenceBundle, RefundEvidence, WebhookEventEvidence

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_MINUTES = 30
SLA_HOURS = 72


def make_evidence(**overrides) -> EvidenceBundle:
    base = dict(
        order_id="order_1",
        order_state="CONFIRMED",
        payment_id="pay_1",
        payment_status="captured",
        payment_captured_at=NOW - timedelta(minutes=1),
        webhook_events=[],
        refunds=[],
        now=NOW,
        fulfillable_window_minutes=WINDOW_MINUTES,
        refund_sla_hours=SLA_HOURS,
    )
    base.update(overrides)
    return EvidenceBundle(**base)


def test_no_webhook_evidence_with_cancelled_order_is_dropped():
    evidence = make_evidence(order_state="CANCELLED", payment_status="captured")

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.WEBHOOK_DROPPED


def test_single_failed_500_event_is_merchant_500():
    evidence = make_evidence(
        webhook_events=[
            WebhookEventEvidence(event_type="payment.captured", delivered=False, delivered_at=NOW, merchant_response_code=500, attempt_number=1)
        ]
    )

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.WEBHOOK_MERCHANT_500


def test_single_delivered_event_past_window_is_delayed():
    evidence = make_evidence(
        payment_captured_at=NOW - timedelta(hours=2),
        webhook_events=[
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW, merchant_response_code=200, attempt_number=1)
        ],
    )

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.WEBHOOK_DELAYED


def test_single_delivered_event_within_window_is_healthy():
    evidence = make_evidence(
        payment_captured_at=NOW - timedelta(minutes=1),
        webhook_events=[
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW, merchant_response_code=200, attempt_number=1)
        ],
    )

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.HEALTHY


def test_attempt_one_after_attempt_two_is_out_of_order():
    evidence = make_evidence(
        webhook_events=[
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW, merchant_response_code=200, attempt_number=2),
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW + timedelta(seconds=1), merchant_response_code=200, attempt_number=1),
        ]
    )

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.WEBHOOK_OUT_OF_ORDER


def test_attempt_two_after_attempt_one_is_duplicated():
    evidence = make_evidence(
        webhook_events=[
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW, merchant_response_code=200, attempt_number=1),
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW + timedelta(seconds=1), merchant_response_code=200, attempt_number=2),
        ]
    )

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.WEBHOOK_DUPLICATED


def test_refund_initiated_past_sla_is_refund_stuck_even_with_clean_webhooks():
    evidence = make_evidence(
        webhook_events=[
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW - timedelta(minutes=1), merchant_response_code=200, attempt_number=1)
        ],
        refunds=[RefundEvidence(gateway_status="initiated", initiated_at=NOW - timedelta(hours=100), settled_at=None)],
    )

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.REFUND_STUCK


def test_refund_initiated_within_sla_does_not_override_healthy():
    evidence = make_evidence(
        webhook_events=[
            WebhookEventEvidence(event_type="payment.captured", delivered=True, delivered_at=NOW - timedelta(minutes=1), merchant_response_code=200, attempt_number=1)
        ],
        refunds=[RefundEvidence(gateway_status="initiated", initiated_at=NOW - timedelta(hours=1), settled_at=None)],
    )

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.HEALTHY


def test_no_evidence_at_all_is_healthy_not_a_false_positive():
    evidence = make_evidence(order_state="CREATED", payment_status=None, payment_captured_at=None, webhook_events=[])

    result = diagnose_deterministic(evidence)

    assert result.root_cause_class == RootCauseClass.HEALTHY
