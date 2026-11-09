"""Read-only PayPal tools for the investigator agent.

The agent may look things up in PayPal, but it can never move money: there is
no refund, capture or void tool here, and every call is an HTTP GET. It also
cannot point a tool at an arbitrary PayPal resource — each lookup is checked
against the ids Limbo already holds for the transaction under investigation,
so text inside a PayPal response cannot steer it toward another merchant's
data (ADR-017: the agent proposes, PolicyEngine decides)."""

import json
from typing import Awaitable, Callable

import httpx

# Signature of PayPalAdapter._request, kept as a plain callable so tests can
# swap in a fake without building an adapter.
PayPalGet = Callable[..., Awaitable[dict]]

TOOL_DEFINITIONS = [
    {
        "name": "get_capture",
        "description": (
            "Fetch a PayPal capture (Payments v2 GET /v2/payments/captures/{id}). Returns its status "
            "(COMPLETED, REFUNDED, PARTIALLY_REFUNDED, PENDING, DECLINED), amount, create/update time "
            "and seller breakdown. Use it to confirm whether the buyer's money was really taken."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"capture_id": {"type": "string", "description": "PayPal capture id from the evidence"}},
            "required": ["capture_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_refund",
        "description": (
            "Fetch a PayPal refund (GET /v2/payments/refunds/{id}). Returns its status (COMPLETED, PENDING, "
            "FAILED, CANCELLED) and amount. Only works for refund ids listed in get_capture's links."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"refund_id": {"type": "string"}},
            "required": ["refund_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_disputes",
        "description": (
            "List PayPal disputes opened against this capture (GET /v1/customer/disputes?disputed_transaction_id=). "
            "An open dispute on a limbo payment means the buyer has already escalated and the case is urgent."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"capture_id": {"type": "string"}},
            "required": ["capture_id"],
            "additionalProperties": False,
        },
    },
]

# Keep tool results small: the agent needs status fields, not PayPal's HATEOAS noise.
_MAX_RESULT_CHARS = 4000
_CAPTURE_FIELDS = ("id", "status", "status_details", "amount", "final_capture", "create_time", "update_time", "seller_receivable_breakdown")
_REFUND_FIELDS = ("id", "status", "status_details", "amount", "create_time", "update_time")


def _pick(body: dict, fields: tuple[str, ...]) -> dict:
    return {k: body[k] for k in fields if k in body}


class PayPalReadOnlyTools:
    def __init__(self, paypal_get: PayPalGet, capture_id: str):
        self._get = paypal_get
        self._capture_id = capture_id
        # Refund ids become allowed only once PayPal itself lists them on this capture.
        self._allowed_refund_ids: set[str] = set()

    async def run(self, name: str, tool_input: dict) -> tuple[str, bool]:
        """Returns (result text, is_error). Never raises: a failed lookup goes
        back to the agent as an error tool_result so it can reason about it."""
        try:
            if name == "get_capture":
                body = await self._capture(tool_input["capture_id"])
            elif name == "get_refund":
                body = await self._refund(tool_input["refund_id"])
            elif name == "list_disputes":
                body = await self._disputes(tool_input["capture_id"])
            else:
                return f"unknown tool {name!r}", True
        except PermissionError as exc:
            return str(exc), True
        except httpx.HTTPStatusError as exc:
            return f"PayPal returned HTTP {exc.response.status_code}: {exc.response.text[:500]}", True
        except (httpx.HTTPError, KeyError) as exc:
            return f"lookup failed: {exc!r}", True
        return json.dumps(body, default=str)[:_MAX_RESULT_CHARS], False

    def _check_capture(self, capture_id: str) -> None:
        if capture_id != self._capture_id:
            raise PermissionError(f"capture {capture_id!r} is not the transaction under investigation ({self._capture_id!r})")

    async def _capture(self, capture_id: str) -> dict:
        self._check_capture(capture_id)
        body = await self._get("GET", f"/v2/payments/captures/{capture_id}")
        for link in body.get("links", []):
            href = link.get("href", "")
            if "/v2/payments/refunds/" in href:
                self._allowed_refund_ids.add(href.rstrip("/").rsplit("/", 1)[-1])
        result = _pick(body, _CAPTURE_FIELDS)
        result["refund_ids"] = sorted(self._allowed_refund_ids)
        return result

    async def _refund(self, refund_id: str) -> dict:
        if refund_id not in self._allowed_refund_ids:
            raise PermissionError(f"refund {refund_id!r} is not linked to this capture; call get_capture first")
        return _pick(await self._get("GET", f"/v2/payments/refunds/{refund_id}"), _REFUND_FIELDS)

    async def _disputes(self, capture_id: str) -> dict:
        self._check_capture(capture_id)
        body = await self._get("GET", "/v1/customer/disputes", params={"disputed_transaction_id": capture_id})
        items = [
            _pick(d, ("dispute_id", "reason", "status", "dispute_state", "dispute_amount", "create_time", "dispute_life_cycle_stage"))
            for d in body.get("items", [])
        ]
        return {"disputes": items, "count": len(items)}
