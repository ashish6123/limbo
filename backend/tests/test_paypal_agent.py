"""PayPal mode end to end, against a fake PayPal sandbox and a fake Gemini:
the investigator's read-only tools, the demo lifecycle (order -> capture ->
dropped webhook -> diagnosis -> real refund call), and the webhook receiver."""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.diagnosis import llm
from app.diagnosis.enums import RootCauseClass
from app.diagnosis.paypal_tools import PayPalReadOnlyTools
from app.diagnosis.schemas import EvidenceBundle
from app.demo import service as demo_service
from app.models.audit_log import AuditLog
from app.models.policy_action import PolicyAction
from app.models.refund import Refund
from app.policy.enums import ActionStatus, ActionType
from tests.test_demo_service import make_settings
from tests.test_paypal_adapter import WEBHOOK_HEADERS


class FakePayPal:
    """In-memory PayPal sandbox: OAuth, Orders v2, captures, refunds, disputes."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.orders: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 32400})
        if method == "POST" and path == "/v2/checkout/orders":
            body = json.loads(request.content)
            oid = f"ORDER{uuid.uuid4().hex[:10].upper()}"
            self.orders[oid] = body
            return httpx.Response(201, json={"id": oid, "status": "PAYER_ACTION_REQUIRED", "links": [
                {"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]})
        if method == "POST" and path.endswith("/capture"):
            oid = path.split("/")[-2]
            unit = self.orders[oid]["purchase_units"][0]
            cid = f"CAP{oid}"
            self.captures[cid] = {"id": cid, "status": "COMPLETED", "amount": unit["amount"],
                                  "custom_id": unit["custom_id"], "create_time": "2026-11-09T10:00:00Z", "links": []}
            return httpx.Response(201, json={"id": oid, "status": "COMPLETED",
                                             "purchase_units": [{"payments": {"captures": [{"id": cid}]}}]})
        if method == "GET" and path.startswith("/v2/payments/captures/"):
            return httpx.Response(200, json=self.captures[path.rsplit("/", 1)[-1]])
        if method == "POST" and path.endswith("/refund"):
            cid = path.split("/")[-2]
            rid = f"REF{cid}"
            self.refunds[rid] = {"id": rid, "status": "COMPLETED", "amount": json.loads(request.content)["amount"]}
            self.captures[cid]["status"] = "REFUNDED"
            self.captures[cid]["links"].append({"rel": "refund", "href": f"https://api-m.sandbox.paypal.com/v2/payments/refunds/{rid}"})
            return httpx.Response(201, json=self.refunds[rid])
        if method == "GET" and path.startswith("/v2/payments/refunds/"):
            return httpx.Response(200, json=self.refunds[path.rsplit("/", 1)[-1]])
        if method == "GET" and path == "/v1/customer/disputes":
            return httpx.Response(200, json={"items": []})
        if path == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": "SUCCESS"})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})


def patched_httpx(fake: FakePayPal):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(fake))
        return real(*args, **kwargs)

    return factory


class FakeGemini:
    """Asks for get_capture and list_disputes, then answers with no tool call,
    then returns the structured conclusion."""

    def __init__(self, capture_id: str):
        self.capture_id = capture_id
        self.calls: list[dict] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        content = SimpleNamespace(role="model", parts=[])
        if n == 1:
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)], text=None, function_calls=[
                SimpleNamespace(name="get_capture", args={"capture_id": self.capture_id}),
                SimpleNamespace(name="list_disputes", args={"capture_id": self.capture_id}),
            ])
        if n == 2:
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)], text="done", function_calls=None)
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)], function_calls=None, text=llm.InvestigationOutput(
            root_cause_class=RootCauseClass.WEBHOOK_DROPPED, confidence=0.93,
            human_readable_narrative="PayPal shows the capture COMPLETED; no webhook reached the merchant.",
            recommended_action="Auto-refund the buyer.", customer_message="We are refunding you.",
            dispute_open=False).model_dump_json())


@pytest.mark.asyncio
async def test_tools_refuse_ids_outside_the_investigation():
    async def get(method, path, **kw):
        return {"id": "CAP1", "status": "COMPLETED", "links": []}

    tools = PayPalReadOnlyTools(get, "CAP1")
    text, err = await tools.run("get_capture", {"capture_id": "SOMEONE_ELSES"})
    assert err and "not the transaction under investigation" in text
    text, err = await tools.run("get_refund", {"refund_id": "REF_UNLINKED"})
    assert err
    text, err = await tools.run("get_capture", {"capture_id": "CAP1"})
    assert not err and json.loads(text)["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_investigator_records_tool_calls_and_returns_structured_result():
    async def get(method, path, **kw):
        return {"items": []} if "disputes" in path else {"id": "CAP1", "status": "COMPLETED", "links": []}

    recorded = []

    async def recorder(name, tool_input, text, is_error):
        recorded.append((name, is_error))

    evidence = EvidenceBundle(order_id="o1", order_state="CANCELLED", payment_id="CAP1", payment_status="captured",
                              payment_captured_at=datetime.now(timezone.utc), webhook_events=[], refunds=[],
                              now=datetime.now(timezone.utc), fulfillable_window_minutes=30, refund_sla_hours=72)
    gemini = FakeGemini("CAP1")
    outcome = await llm.diagnose_llm(evidence, gemini, PayPalReadOnlyTools(get, "CAP1"), recorder)
    assert outcome is not None
    result, investigation = outcome
    assert result.root_cause_class == RootCauseClass.WEBHOOK_DROPPED
    assert recorded == [("get_capture", False), ("list_disputes", False)]
    # The concluding call offers no tools and sees the PayPal lookups as data.
    final = gemini.calls[-1]
    assert final["config"].tools is None
    assert "paypal_lookups" in final["contents"] and "list_disputes" in final["contents"]


@pytest.mark.asyncio
async def test_paypal_refund_scenario_end_to_end(session):
    fake = FakePayPal()
    settings = make_settings(gateway_mode="paypal", paypal_client_id="id", paypal_client_secret="secret",
                             gemini_api_key="sk-test", demo_order_amount_paise=1000, demo_max_amount_paise=1000)
    with patch.object(demo_service.httpx, "AsyncClient", patched_httpx(fake)):
        demo_session, checkout_url = await demo_service.create_order(
            session, settings, demo_service.SCENARIO_REFUND, return_url="https://dash.example/demo/return")
        assert checkout_url.startswith("https://www.sandbox.paypal.com/")
        unit = fake.orders[demo_session.gateway_reference]["purchase_units"][0]
        assert unit["custom_id"] == demo_session.order_id and unit["amount"] == {"value": "10.00", "currency_code": "USD"}

        payment = await demo_service.drop_webhook(session, settings, demo_session)
        assert payment.method == "paypal" and payment.id.startswith("CAP")

        with patch("app.diagnosis.service.make_client", lambda api_key: FakeGemini(payment.id)):
            await demo_service.drive_scenario(session, settings, demo_session, payment)

    action = (await session.execute(select(PolicyAction).where(PolicyAction.order_id == demo_session.order_id))).scalars().first()
    assert action.action_type == ActionType.AUTO_REFUND and action.status == ActionStatus.EXECUTED
    refund = (await session.execute(select(Refund).where(Refund.payment_id == payment.id))).scalars().first()
    assert refund is not None and refund.id in fake.refunds
    events = [r.event for r in (await session.execute(select(AuditLog).where(AuditLog.order_id == demo_session.order_id))).scalars()]
    assert "paypal.capture.completed" in events
    assert "paypal.get_capture" in events and "paypal.list_disputes" in events
    # The agent only ever read; the one money-moving call came from the policy engine.
    money_calls = [r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/refund")]
    assert len(money_calls) == 1


@pytest.mark.asyncio
async def test_webhook_dispute_on_tracked_payment_escalates_and_dedupes(session):
    import uuid

    from app.api import paypal_webhooks
    from app.main import app
    from app.merchant.service import MerchantService
    from app.models.enums import PaymentGatewayStatus
    from app.models.payment import Payment

    order_id = f"order_pp_{uuid.uuid4().hex[:8]}"
    capture_id = f"CAP_{uuid.uuid4().hex[:8]}"
    await MerchantService(session).create_order(order_id, 1000, currency="USD")
    session.add(Payment(id=capture_id, order_id=order_id, amount_paise=1000, gateway_status=PaymentGatewayStatus.CAPTURED,
                        method="paypal", captured_at=datetime.now(timezone.utc)))
    await session.commit()

    event = {"id": f"WH-{uuid.uuid4().hex[:8]}", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {
        "dispute_id": "PP-D-1", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "disputed_transactions": [{"seller_transaction_id": capture_id}]}}
    fake = FakePayPal()
    s = paypal_webhooks.settings
    with patch.object(s, "gateway_mode", "paypal"), patch.object(s, "paypal_webhook_id", "WH_ID"), \
            patch.object(s, "gemini_api_key", ""), \
            patch.object(paypal_webhooks.httpx, "AsyncClient", patched_httpx(fake)):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/webhooks/paypal", json=event, headers=WEBHOOK_HEADERS)
            second = await client.post("/webhooks/paypal", json=event, headers=WEBHOOK_HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"

    escalated = (await session.execute(select(PolicyAction).where(PolicyAction.order_id == order_id))).scalars().all()
    assert [(a.action_type, a.status) for a in escalated] == [(ActionType.ESCALATE_HUMAN, ActionStatus.ESCALATED)]
