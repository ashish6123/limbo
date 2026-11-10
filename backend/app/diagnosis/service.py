from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.diagnosis.deterministic import diagnose_deterministic
from app.diagnosis.llm import diagnose_llm, make_client
from app.diagnosis.paypal_tools import PayPalGet, PayPalReadOnlyTools
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
        gemini_api_key: str = "",
        time_provider: TimeProvider = SystemTimeProvider(),
        paypal_get: PayPalGet | None = None,
        commit_tool_calls: bool = False,
    ):
        # paypal_get gives the investigator read-only PayPal lookups (GATEWAY_MODE=paypal).
        # commit_tool_calls makes each tool step visible to the live demo stream at once.
        self._paypal_get = paypal_get
        self._commit_tool_calls = commit_tool_calls
        self._session = session
        self._fulfillable_window_minutes = fulfillable_window_minutes
        self._refund_sla_hours = refund_sla_hours
        self._time = time_provider
        self._llm_client = make_client(gemini_api_key) if gemini_api_key else None

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
        investigation = None
        source = "deterministic"
        if self._llm_client is not None:
            tools = None
            if self._paypal_get is not None and evidence.payment_id:
                tools = PayPalReadOnlyTools(self._paypal_get, evidence.payment_id)

            async def record_tool_call(name: str, tool_input: dict, text: str, is_error: bool) -> None:
                await audit.record(
                    self._session,
                    stage="investigation",
                    event=f"paypal.{name}" + (".error" if is_error else ""),
                    order_id=order_id,
                    payment_id=evidence.payment_id,
                    inputs={"tool": name, "input": tool_input},
                    reasoning=text[:1000],
                    created_at=self._time.now(),
                )
                if self._commit_tool_calls:
                    await self._session.commit()

            outcome = await diagnose_llm(evidence, self._llm_client, tools, record_tool_call)
            if outcome is not None:
                result, investigation = outcome
                source = "llm+paypal_tools" if tools is not None else "llm"

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
            inputs={
                "source": source,
                "confidence": result.confidence,
                "recommended_action": result.recommended_action,
                **(
                    {"customer_message_draft": investigation.customer_message, "dispute_open": investigation.dispute_open}
                    if investigation is not None
                    else {}
                ),
            },
            reasoning=result.human_readable_narrative,
            created_at=self._time.now(),
        )
        await self._session.flush()

        return result, source
