"""Structured logging. JSON in production, human-readable locally.

Every log line carries the request id when one is in scope, so a single request
can be followed end to end across the API layer and the upstream fetches.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import orjson

from .config import get_settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Fields LogRecord always carries; anything else was passed via extra=.
_STD = frozenset(
    """name msg args levelname levelno pathname filename module exc_info exc_text
    stack_info lineno funcName created msecs relativeCreated thread threadName
    processName process taskName message asctime""".split()
)

# Never emit these, whatever the caller passes.
_REDACT = ("token", "secret", "password", "authorization", "api_key", "apikey", "cookie")


def _redact(key: str, value: Any) -> Any:
    return "***" if any(marker in key.lower() for marker in _REDACT) else value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "service": get_settings().service_name,
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key not in _STD and not key.startswith("_"):
                payload[key] = _redact(key, value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return orjson.dumps(payload).decode()


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name:<28} {record.getMessage()}"
        extras = {
            k: _redact(k, v)
            for k, v in record.__dict__.items()
            if k not in _STD and not k.startswith("_")
        }
        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging() -> None:
    s = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if s.log_format == "json" else TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(s.log_level)

    # These are noisy at INFO and say nothing our own access log does not.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
