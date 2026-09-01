import razorpay
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.gateway.base import GatewayAdapter, WebhookSink
from app.gateway.mock import MockGatewayAdapter
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
    raise ValueError(f"unknown GATEWAY_MODE {settings.gateway_mode!r}, expected 'mock' or 'razorpay'")
