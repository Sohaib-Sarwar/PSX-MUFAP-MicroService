"""Error responses that say what went wrong without leaking internals.

A handler never returns str(exception) to a client: that is how upstream URLs
and stack detail end up in public responses. Clients get a stable machine code
and the request id; the detail goes to the log.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .http import UpstreamError
from .logging_setup import request_id_var

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Raised by domain code; carries a client-safe message and code."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "internal_error"

    def __init__(self, message: str, *, code: str | None = None,
                 status_code: int | None = None) -> None:
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        super().__init__(message)


class DataUnavailable(ServiceError):
    """No usable snapshot yet. 503, not 404 — the resource exists, it is warming."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "data_unavailable"


class NotFound(ServiceError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class BadRequest(ServiceError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"


def error_body(code: str, message: str) -> dict:
    payload = {"error": {"code": code, "message": message}}
    rid = request_id_var.get()
    if rid:
        payload["error"]["request_id"] = rid
    return payload


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service(_: Request, exc: ServiceError) -> ORJSONResponse:
        return ORJSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)

    @app.exception_handler(UpstreamError)
    async def _upstream(_: Request, exc: UpstreamError) -> ORJSONResponse:
        # The upstream URL and failure reason are operational detail, not public.
        logger.error("upstream_failed", extra={"url": exc.url, "reason": exc.reason})
        return ORJSONResponse(
            error_body("upstream_unavailable", "An upstream data source is unavailable."),
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> ORJSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ()) if p not in ("query", "body"))
        message = f"Invalid value for '{field}': {first.get('msg', 'invalid')}" if field else "Invalid request."
        return ORJSONResponse(error_body("validation_error", message),
                              status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> ORJSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}
        return ORJSONResponse(
            error_body(codes.get(exc.status_code, "http_error"), str(exc.detail)),
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> ORJSONResponse:
        logger.exception("unhandled_exception", extra={"error": type(exc).__name__})
        return ORJSONResponse(
            error_body("internal_error", "An unexpected error occurred."),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
