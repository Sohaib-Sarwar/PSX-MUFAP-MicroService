"""Request identity, rate limiting and the shared secret guarding /internal/*."""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections import deque

from fastapi import Header, Request, status
from fastapi.responses import ORJSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import get_settings
from .errors import ServiceError, error_body
from .logging_setup import request_id_var

access_logger = logging.getLogger("access")


class Unauthorized(ServiceError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


async def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Guard for refresh endpoints and the Vercel Cron hook.

    When INTERNAL_TOKEN is unset these routes are refused outright rather than
    left open. An unauthenticated refresh endpoint is a way to drive unbounded
    traffic at a third-party site from this service's IP address.
    """
    expected = get_settings().internal_token
    if not expected:
        raise Unauthorized("Internal endpoints are disabled (INTERNAL_TOKEN is not set).")
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise Unauthorized("Invalid or missing X-Internal-Token.")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, emits one access log line."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = rid
        access_logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 2),
            },
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limiter, per client IP.

    Deliberately in-process and approximate: it exists to stop one client
    hammering the service, not to meter billing. On serverless each instance
    keeps its own window, which is acceptable for that purpose.
    """

    def __init__(self, app, limit_per_minute: int) -> None:
        super().__init__(app)
        self._limit = limit_per_minute
        self._hits: dict[str, deque] = {}

    async def dispatch(self, request: Request, call_next):
        if self._limit <= 0 or request.url.path in {"/health", "/ready"}:
            return await call_next(request)

        forwarded = request.headers.get("X-Forwarded-For", "")
        client = forwarded.split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )

        now = time.monotonic()
        window = self._hits.setdefault(client, deque())
        while window and now - window[0] > 60.0:
            window.popleft()

        if len(window) >= self._limit:
            return ORJSONResponse(
                error_body("rate_limited", "Too many requests. Slow down."),
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": "60"},
            )

        window.append(now)

        # Bound the dict so a spray of unique client IPs cannot grow it without limit.
        if len(self._hits) > 10_000:
            stale = [k for k, v in self._hits.items() if not v or now - v[-1] > 120]
            for key in stale[:5000]:
                self._hits.pop(key, None)

        return await call_next(request)
