from datetime import timedelta

from app.diagnosis.enums import RootCauseClass
from app.diagnosis.schemas import DiagnosisResult, EvidenceBundle


def diagnose_deterministic(evidence: EvidenceBundle) -> DiagnosisResult:
    """Rule-based root-cause classification from observable webhook/payment/refund
    evidence only — the same evidence the LLM path sees, never chaos ground truth.
    Used both as the offline fallback and, when it disagrees with the LLM, as a
    signal that the LLM path may be misreading the evidence."""
    refund_sla = timedelta(hours=evidence.refund_sla_hours)
    stuck_refund = any(
        r.gateway_status == "initiated" and r.settled_at is None and evidence.now - r.initiated_at >= refund_sla
        for r in evidence.refunds
    )
    if stuck_refund:
        return DiagnosisResult(
            root_cause_class=RootCauseClass.REFUND_STUCK,
            confidence=0.95,
            human_readable_narrative="A refund was initiated but its settlement callback never arrived within the SLA window.",
            recommended_action="Retrigger refund settlement with the gateway.",
        )

    capture_events = [e for e in evidence.webhook_events if e.event_type == "payment.captured"]

    if not capture_events:
        if evidence.payment_status == "captured" and evidence.order_state == "CANCELLED":
            return DiagnosisResult(
                root_cause_class=RootCauseClass.WEBHOOK_DROPPED,
                confidence=0.9,
                human_readable_narrative="The gateway captured the payment but no webhook was ever recorded as delivered, and the order was independently cancelled.",
                recommended_action="Replay the payment.captured webhook if still within the fulfillable window, otherwise auto-refund.",
            )
        return DiagnosisResult(
            root_cause_class=RootCauseClass.HEALTHY,
            confidence=0.6,
            human_readable_narrative="No webhook evidence and no captured payment against a cancelled order — nothing indicates a mismatch.",
            recommended_action="No action needed.",
        )

    if len(capture_events) == 1:
        event = capture_events[0]
        if not event.delivered and event.merchant_response_code == 500:
            return DiagnosisResult(
                root_cause_class=RootCauseClass.WEBHOOK_MERCHANT_500,
                confidence=0.9,
                human_readable_narrative="The webhook reached the merchant, which returned a 500 — its handler likely crashed before committing the order state.",
                recommended_action="Replay the webhook if still within the fulfillable window, otherwise auto-refund.",
            )
        if event.delivered and event.delivered_at and evidence.payment_captured_at:
            lag = event.delivered_at - evidence.payment_captured_at
            window = timedelta(minutes=evidence.fulfillable_window_minutes)
            if lag >= window:
                return DiagnosisResult(
                    root_cause_class=RootCauseClass.WEBHOOK_DELAYED,
                    confidence=0.85,
                    human_readable_narrative=f"The webhook was delivered, but {lag} after capture — past the fulfillable window.",
                    recommended_action="Auto-refund; the order likely already timed out before the webhook arrived.",
                )
        return DiagnosisResult(
            root_cause_class=RootCauseClass.HEALTHY,
            confidence=0.9,
            human_readable_narrative="A single webhook was delivered promptly and successfully.",
            recommended_action="No action needed.",
        )

    if len(capture_events) == 2:
        by_attempt = {e.attempt_number: e for e in capture_events}
        first, second = by_attempt.get(1), by_attempt.get(2)
        if first and second and first.delivered_at and second.delivered_at:
            if first.delivered_at > second.delivered_at:
                return DiagnosisResult(
                    root_cause_class=RootCauseClass.WEBHOOK_OUT_OF_ORDER,
                    confidence=0.85,
                    human_readable_narrative="The retry (attempt 2) was delivered before the original (attempt 1) — likely network reordering.",
                    recommended_action="No action needed; the order confirmed correctly regardless of delivery order.",
                )
            return DiagnosisResult(
                root_cause_class=RootCauseClass.WEBHOOK_DUPLICATED,
                confidence=0.85,
                human_readable_narrative="The same webhook event was delivered twice.",
                recommended_action="No action needed; duplicate delivery was handled idempotently.",
            )

    return DiagnosisResult(
        root_cause_class=RootCauseClass.UNKNOWN,
        confidence=0.3,
        human_readable_narrative="The observable evidence does not match a known failure pattern.",
        recommended_action="Escalate for human review.",
    )
