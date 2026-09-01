from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from app.gateway.schemas import WebhookPayload

WebhookSink = Callable[[WebhookPayload], Awaitable[None]]


class GatewayAdapter(ABC):
    """Common interface implemented by both the mock gateway and the real Razorpay adapter."""

    @abstractmethod
    async def capture_payment(self, order_id: str, amount_paise: int, method: str) -> str:
        """Simulates a successful customer payment. Returns the gateway payment id."""

    @abstractmethod
    async def create_refund(self, payment_id: str, amount_paise: int) -> str:
        """Initiates a refund for a captured payment. Returns the gateway refund id."""

    @abstractmethod
    async def retry_refund_settlement(self, refund_id: str) -> None:
        """RETRIGGER_REFUND recovery action: re-checks/nudges a refund stuck in
        'initiated' status."""
