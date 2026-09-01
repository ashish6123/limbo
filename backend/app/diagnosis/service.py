from datetime import datetime

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.diagnosis.deterministic import diagnose_deterministic
from app.diagnosis.llm import diagnose_llm
from app.diagnosis.schemas import DiagnosisResult, EvidenceBundle, RefundEvidence, WebhookEventEvidence
from app.models.diagnosis_record import DiagnosisRecord
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.webhook_event import WebhookEvent
from app.time_provider import SystemTimeProvider, TimeProvider


class DiagnosisEngine:
    """Builds the observable evidence bundle for a transaction and produces a
    root-cause diagnosis — via the LLM when an API key is configured, falling
    back to the deterministic classifier otherwise or on any LLM failure. Never
    reads chaos injection ground truth (see docs/decisions.md ADR-007)."""

    def __init__(
        self,
        session: AsyncSession,
        fulfillable_window_minutes: int,
        refund_sla_hours: int,
        anthropic_api_key: str = "",
        time_provider: TimeProvider = SystemTimeProvider(),
    ):
        self._session = session
        self._fulfillable_window_minutes = fulfillable_window_minutes
        self._refund_sla_hours = refund_sla_hours
        self._time = time_provider
        self._llm_client = anthropic.AsyncAnthropic(api_key=anthropic_api_key) if anthropic_api_key else None

    async def build_evidence(self, order_id: str) -> EvidenceBundle:
        order = await self._session.get(Order, order_id)
        if order is None:
            raise ValueError(f"cannot diagnose unknown order {order_id!r}")

        payment = (
            await self._session.execute(select(Payment).where(Payment.order_id == order_id))
        ).scalars().first()

        webhook_events: list[WebhookEventEvidence] = []
        refunds: list[RefundEvidence] = []
        if payment is not None:
            events = (
                await self._session.execute(select(WebhookEvent).where(WebhookEvent.payment_id == payment.id))
            ).scalars().all()
            webhook_events = [
                WebhookEventEvidence(
                    event_type=e.event_type.value,
                    delivered=e.delivered,
                    delivered_at=e.delivered_at,
                    merchant_response_code=e.merchant_response_code,
                    attempt_number=e.attempt_number,
                )
                for e in events
            ]
            refund_rows = (
                await self._session.execute(select(Refund).where(Refund.payment_id == payment.id))
            ).scalars().all()
            refunds = [
                RefundEvidence(gateway_status=r.gateway_status.value, initiated_at=r.initiated_at, settled_at=r.settled_at)
                for r in refund_rows
            ]

        return EvidenceBundle(
            order_id=order_id,
            order_state=order.state.value,
            payment_id=payment.id if payment else None,
            payment_status=payment.gateway_status.value if payment else None,
            payment_captured_at=payment.captured_at if payment else None,
            webhook_events=webhook_events,
            refunds=refunds,
            now=self._time.now(),
            fulfillable_window_minutes=self._fulfillable_window_minutes,
            refund_sla_hours=self._refund_sla_hours,
        )

    async def diagnose(self, order_id: str) -> tuple[DiagnosisResult, str]:
        evidence = await self.build_evidence(order_id)

        result = None
        source = "deterministic"
        if self._llm_client is not None:
            result = await diagnose_llm(evidence, self._llm_client)
            source = "llm"

        if result is None:
            result = diagnose_deterministic(evidence)
            source = "deterministic"

        self._session.add(
            DiagnosisRecord(
                order_id=order_id,
                payment_id=evidence.payment_id,
                root_cause_class=result.root_cause_class,
                confidence=result.confidence,
                narrative=result.human_readable_narrative,
                recommended_action=result.recommended_action,
                source=source,
                created_at=self._time.now(),
            )
        )
        await audit.record(
            self._session,
            stage="diagnosis",
            event=result.root_cause_class.value,
            order_id=order_id,
            payment_id=evidence.payment_id,
            inputs={"source": source, "confidence": result.confidence},
            reasoning=result.human_readable_narrative,
            created_at=self._time.now(),
        )
        await self._session.flush()

        return result, source
