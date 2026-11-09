import httpx
import razorpay
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.gateway.base import GatewayAdapter, WebhookSink
from app.gateway.mock import MockGatewayAdapter
from app.gateway.paypal_adapter import PayPalAdapter
from app.gateway.razorpay_adapter import RazorpayAdapter
from app.time_provider import SystemTimeProvider, TimeProvider


def build_gateway_adapter(
    settings: Settings,
    session: AsyncSession,
    webhook_sink: WebhookSink,
    time_provider: TimeProvider = SystemTimeProvider(),
) -> GatewayAdapter:
    if settings.gateway_mode == "mock":
        return MockGatewayAdapter(session, webhook_sink, time_provider)
    if settings.gateway_mode == "razorpay":
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise ValueError("GATEWAY_MODE=razorpay requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")
        client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
        return RazorpayAdapter(session, client, webhook_sink)
    if settings.gateway_mode == "paypal":
        if not settings.paypal_client_id or not settings.paypal_client_secret:
            raise ValueError("GATEWAY_MODE=paypal requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
        http = httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=30.0)
        return PayPalAdapter(
            session,
            http,
            settings.paypal_client_id,
            settings.paypal_client_secret,
            webhook_sink,
            currency=settings.paypal_currency,
        )
    raise ValueError(f"unknown GATEWAY_MODE {settings.gateway_mode!r}, expected 'mock', 'razorpay' or 'paypal'")
