"""Browser-profile HTTP client, used only for MUFAP.

MUFAP's robots.txt permits this access outright:

    User-agent: *
    Content-Signal: search=yes, ai-train=no, use=reference
    Allow: /

but the site sits behind Cloudflare bot management, which rejects the request
on its TLS fingerprint rather than anything in the HTTP layer. Measured on
2026-09-13 against https://www.mufap.com.pk/Industry/IndustryStatDaily?tab=1:

    httpx, minimal headers          403  (5,838 B challenge page)
    httpx, full Chrome header set   403  (3,479 B challenge page)
    httpx, tuned TLS cipher list    403  (5,838 B challenge page)
    curl (shell), same headers      200  (1,271,966 B)
    curl_cffi, chrome TLS profile   200  (1,272,021 B)

So the header set is not the discriminator and neither is the source IP — the
TLS handshake is. curl_cffi negotiates a standard browser TLS profile, which is
what makes a permitted request actually succeed.

PSX needs none of this and stays on plain httpx.

If curl_cffi is unavailable, or the request is blocked anyway, this raises
UpstreamError and the caller keeps serving its last-known-good snapshot marked
`degraded`. The service never goes down because one upstream declined.
"""

from __future__ import annotations

import asyncio
import logging

from .config import get_settings
from .http import UpstreamError, fetch_text as fetch_text_httpx

logger = logging.getLogger(__name__)

_warned = False


def _impersonating_client():
    try:
        from curl_cffi import requests as curl_requests  # noqa: WPS433
    except ImportError:
        return None
    return curl_requests


async def fetch_text_browser(url: str) -> str:
    """Fetch a page using a browser TLS profile, falling back to httpx.

    `MUFAP_HTTP_CLIENT=httpx` forces the plain client, for an environment where
    the extra dependency is unwanted or where access has been arranged directly.
    """
    global _warned
    settings = get_settings()

    if settings.mufap_http_client == "httpx":
        return await fetch_text_httpx(url)

    client = _impersonating_client()
    if client is None:
        if not _warned:
            logger.warning(
                "curl_cffi_unavailable",
                extra={"detail": "falling back to httpx; MUFAP will likely return 403"},
            )
            _warned = True
        return await fetch_text_httpx(url)

    attempts = settings.http_max_retries + 1
    last_status: int | None = None

    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(min(settings.http_backoff_s * (2 ** (attempt - 1)), 4.0))
        try:
            # curl_cffi is synchronous; run it off the event loop.
            response = await asyncio.to_thread(
                client.get,
                url,
                impersonate=settings.mufap_impersonate,
                timeout=settings.http_timeout_s,
            )
        except Exception as exc:
            logger.warning("mufap_fetch_error",
                           extra={"error": type(exc).__name__, "attempt": attempt + 1})
            last_status = None
            continue

        if response.status_code == 200:
            return response.text

        last_status = response.status_code
        if response.status_code == 403:
            # A challenge. Backing off is the polite response; hammering it is
            # what turns a soft block into a hard one.
            logger.warning("mufap_challenged",
                           extra={"status": 403, "attempt": attempt + 1})
            continue
        if response.status_code < 500:
            break

    raise UpstreamError(
        url,
        f"blocked by upstream bot management after {attempts} attempts",
        last_status,
    )
