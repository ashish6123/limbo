import time
from collections import defaultdict

from fastapi import HTTPException, Request

from app.config import settings

_WINDOW_SECONDS = 60.0
_hits: dict[str, list[float]] = defaultdict(list)


def enforce_rate_limit(request: Request) -> None:
    """Simple in-memory fixed-window limiter keyed by client IP, so the public
    demo can't be used to spam Razorpay's test API or exhaust the demo
    account's test balance (see docs/decisions.md ADR-019). In-memory is
    sufficient here: the deployed API runs as a single Render web service
    instance, and this limiter only needs to survive one process's lifetime."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = _hits[client_ip]
    hits[:] = [t for t in hits if now - t < _WINDOW_SECONDS]
    if len(hits) >= settings.demo_rate_limit_per_minute:
        raise HTTPException(status_code=429, detail="too many demo requests, try again in a minute")
    hits.append(now)
