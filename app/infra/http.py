"""Shared async HTTP client: pooled connections, bounded retries, explicit timeouts.

Every upstream fetch in this service goes through `fetch_text` or `fetch_json`.
No call is unbounded: a hard cap on attempts and a hard timeout per attempt mean
the worst-case cost of one fetch is knowable and small.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

# Retrying these is worthwhile; anything else is a definite answer.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class UpstreamError(RuntimeError):
    """An upstream fetch failed after exhausting its retry budget."""

    def __init__(self, url: str, reason: str, status: int | None = None) -> None:
        self.url = url
        self.reason = reason
        self.status = status
        super().__init__(f"{url}: {reason}")


def _build_client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        timeout=httpx.Timeout(s.http_timeout_s, connect=s.http_connect_timeout_s),
        limits=httpx.Limits(
            max_connections=s.http_max_connections,
            max_keepalive_connections=s.http_max_connections,
            keepalive_expiry=20.0,  # PSX advertises Keep-Alive: timeout=20
        ),
        headers={
            "User-Agent": s.http_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            # Both upstreams gzip well: market-watch 479KB -> ~60KB on the wire.
            "Accept-Encoding": "gzip, deflate",
        },
        follow_redirects=True,
    )


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = _build_client()
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _request(url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
    s = get_settings()
    client = get_client()
    attempts = s.http_max_retries + 1
    last: Exception | None = None

    for attempt in range(attempts):
        if attempt:
            # Exponential backoff, capped so a bad upstream cannot stall startup.
            await asyncio.sleep(min(s.http_backoff_s * (2 ** (attempt - 1)), 4.0))
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code in _RETRY_STATUS and attempt < attempts - 1:
                logger.warning(
                    "upstream_retry", extra={"url": url, "status": resp.status_code,
                                             "attempt": attempt + 1}
                )
                last = UpstreamError(url, f"HTTP {resp.status_code}", resp.status_code)
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            # 403 from MUFAP means Cloudflare challenged us. Retrying immediately
            # makes that worse, so surface it straight away.
            raise UpstreamError(url, f"HTTP {exc.response.status_code}",
                                exc.response.status_code) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            logger.warning(
                "upstream_error", extra={"url": url, "error": type(exc).__name__,
                                         "attempt": attempt + 1}
            )

    raise UpstreamError(url, f"exhausted {attempts} attempts: {last}")


async def fetch_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    resp = await _request(url, headers=headers)
    return resp.text


async def fetch_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    resp = await _request(url, headers={"Accept": "application/json", **(headers or {})})
    try:
        return resp.json()
    except ValueError as exc:
        raise UpstreamError(url, f"response was not JSON: {exc}") from exc
