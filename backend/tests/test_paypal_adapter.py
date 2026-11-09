import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.gateway.factory import build_gateway_adapter
from app.gateway.paypal_adapter import PayPalAdapter, to_decimal_string, to_minor_units
from app.models.enums import PaymentGatewayStatus, RefundGatewayStatus, WebhookEventType
from app.models.payment import Payment
from app.models.refund import Refund

SANDBOX = "https://api-m.sandbox.paypal.com"

WEBHOOK_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-fake",
    "PAYPAL-TRANSMISSION-ID": "tx-fake",
    "PAYPAL-TRANSMISSION-SIG": "sig-fake",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-25T10:00:00Z",
}


class FakeSession:
    """Just enough of AsyncSession for the adapter: add, get and flush, in memory."""

    def __init__(self, *rows):
        self.rows = {(type(r), r.id): r for r in rows}

    def add(self, row):
        self.rows[(type(row), row.id)] = row

    async def get(self, model, key):
        return self.rows.get((model, key))

    async def flush(self):
        pass


def make_adapter(handler, session=None, sink=None) -> tuple[PayPalAdapter, list]:
    calls = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "token-fake", "expires_in": 32400})
        return handler(request)

    async def noop_sink(payload):
        pass

    http = httpx.AsyncClient(base_url=SANDBOX, transport=httpx.MockTransport(recording_handler))
    adapter = PayPalAdapter(session or FakeSession(), http, "client-id", "client-secret", sink or noop_sink)
    return adapter, calls


def captured_payment(capture_id: str = "CAPTURE1") -> Payment:
    return Payment(
        id=capture_id, order_id="order_1", amount_paise=1000,
        gateway_status=PaymentGatewayStatus.CAPTURED, method="paypal",
    )


def test_amount_conversion_round_trips():
    assert to_minor_units("10.00") == 1000
    assert to_minor_units("0.07") == 7
    assert to_minor_units("12.345") == 1235
    assert to_decimal_string(1000) == "10.00"
    assert to_decimal_string(7) == "0.07"


@pytest.mark.asyncio
async def test_full_refund_writes_ledger_and_emits_webhook():
    def handler(request):
        if request.method == "POST" and request.url.path == "/v2/payments/captures/CAPTURE1/refund":
            assert json.loads(request.content) == {"amount": {"value": "10.00", "currency_code": "USD"}}
            assert request.headers["PayPal-Request-Id"] == "CAPTURE1:refund:1000"
            assert request.headers["Authorization"] == "Bearer token-fake"
            return httpx.Response(201, json={
                "id": "REFUND1", "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "USD"},
                "create_time": "2026-10-25T10:00:00Z",
            })
        if request.method == "GET" and request.url.path == "/v2/payments/captures/CAPTURE1":
            return httpx.Response(200, json={"id": "CAPTURE1", "status": "REFUNDED"})
        return httpx.Response(404)

    emitted = []

    async def sink(payload):
        emitted.append(payload)

    session = FakeSession(captured_payment())
    adapter, _ = make_adapter(handler, session, sink)

    assert await adapter.create_refund("CAPTURE1", 1000) == "REFUND1"

    refund = await session.get(Refund, "REFUND1")
    assert refund.amount_paise == 1000
    assert refund.gateway_status == RefundGatewayStatus.PROCESSED
    assert refund.settled_at is not None
    assert (await session.get(Payment, "CAPTURE1")).gateway_status == PaymentGatewayStatus.REFUNDED
    assert emitted[0].event == WebhookEventType.REFUND_PROCESSED
    assert emitted[0].payload["refund"]["entity"].status == "processed"


@pytest.mark.asyncio
async def test_partial_or_pending_refund_leaves_payment_captured():
    def handler(request):
        if request.method == "POST":
            # No amount/create_time: PayPal's minimal response without Prefer: return=representation.
            return httpx.Response(201, json={"id": "REFUND2", "status": "PENDING"})
        return httpx.Response(200, json={"id": "CAPTURE1", "status": "PARTIALLY_REFUNDED"})

    session = FakeSession(captured_payment())
    adapter, _ = make_adapter(handler, session)

    await adapter.create_refund("CAPTURE1", 400)

    refund = await session.get(Refund, "REFUND2")
    assert refund.amount_paise == 400
    assert refund.gateway_status == RefundGatewayStatus.INITIATED
    assert refund.settled_at is None
    assert (await session.get(Payment, "CAPTURE1")).gateway_status == PaymentGatewayStatus.CAPTURED


@pytest.mark.asyncio
async def test_access_token_is_cached_across_calls():
    def handler(request):
        return httpx.Response(200, json={"id": "CAPTURE1", "status": "COMPLETED"})

    adapter, calls = make_adapter(handler)
    await adapter.fetch_existing_capture("CAPTURE1")
    await adapter.fetch_existing_capture("CAPTURE1")

    assert [c.url.path for c in calls].count("/v1/oauth2/token") == 1


@pytest.mark.asyncio
async def test_retry_settlement_raises_while_refund_still_pending():
    adapter, _ = make_adapter(lambda r: httpx.Response(200, json={"id": "REFUND1", "status": "PENDING"}))
    with pytest.raises(RuntimeError, match="PENDING"):
        await adapter.retry_refund_settlement("REFUND1")


@pytest.mark.asyncio
async def test_create_checkout_order_returns_id_and_buyer_approval_link():
    def handler(request):
        return httpx.Response(200, json={
            "id": "ORDER1",
            "links": [
                {"rel": "self", "href": f"{SANDBOX}/v2/checkout/orders/ORDER1"},
                {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER1"},
            ],
        })

    adapter, calls = make_adapter(handler)
    order_id, approve_url = await adapter.create_checkout_order("order_1", 1000, "https://example.com/return")

    assert order_id == "ORDER1"
    assert approve_url == "https://www.sandbox.paypal.com/checkoutnow?token=ORDER1"
    sent = json.loads(calls[-1].content)
    assert sent["intent"] == "CAPTURE"
    assert sent["purchase_units"][0]["amount"] == {"value": "10.00", "currency_code": "USD"}


@pytest.mark.asyncio
async def test_capture_order_returns_the_capture_id_not_the_order_id():
    adapter, calls = make_adapter(lambda r: httpx.Response(201, json={
        "id": "ORDER1",
        "purchase_units": [{"payments": {"captures": [{"id": "CAPTURE1", "status": "COMPLETED"}]}}],
    }))

    assert await adapter.capture_order("ORDER1") == "CAPTURE1"
    assert calls[-1].url.path == "/v2/checkout/orders/ORDER1/capture"
    assert calls[-1].headers["PayPal-Request-Id"] == "ORDER1:capture"


@pytest.mark.asyncio
async def test_capture_payment_needs_buyer_approval():
    adapter, _ = make_adapter(lambda r: httpx.Response(404))
    with pytest.raises(NotImplementedError):
        await adapter.capture_payment("order_1", 1000, "paypal")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("SUCCESS", True), ("FAILURE", False)])
async def test_webhook_verification_follows_paypal_verdict(status, expected):
    def handler(request):
        body = json.loads(request.content)
        assert body["webhook_id"] == "WH-1"
        assert body["transmission_id"] == "tx-fake"
        return httpx.Response(200, json={"verification_status": status})

    adapter, _ = make_adapter(handler)
    assert await adapter.verify_webhook_signature(WEBHOOK_HEADERS, {"id": "WH-EVT"}, "WH-1") is expected


@pytest.mark.asyncio
async def test_webhook_missing_transmission_headers_is_rejected_without_calling_paypal():
    adapter, calls = make_adapter(lambda r: httpx.Response(200, json={"verification_status": "SUCCESS"}))
    assert await adapter.verify_webhook_signature({}, {"id": "WH-EVT"}, "WH-1") is False
    assert calls == []


def test_live_paypal_base_url_is_rejected():
    with pytest.raises(ValidationError, match="sandbox"):
        Settings(database_url="postgresql+asyncpg://x", paypal_base_url="https://api-m.paypal.com")


def test_factory_builds_paypal_adapter_and_requires_credentials():
    async def sink(payload):
        pass

    settings = Settings(
        database_url="postgresql+asyncpg://x", gateway_mode="paypal",
        paypal_client_id="client-id", paypal_client_secret="client-secret",
    )
    assert isinstance(build_gateway_adapter(settings, None, sink), PayPalAdapter)

    with pytest.raises(ValueError, match="PAYPAL_CLIENT_ID"):
        build_gateway_adapter(settings.model_copy(update={"paypal_client_secret": ""}), None, sink)
