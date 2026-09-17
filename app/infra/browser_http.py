"""Browser-profile HTTP client, used only for MUFAP.

MUFAP's robots.txt permits this access outright:

    User-agent: *
    Content-Signal: search=yes, ai-train=no, use=reference
    Allow: /

but the site sits behind Cloudflare bot management, which scores the request on
signals the HTTP layer never sees. Measured on 2026-09-13 from a Pakistani
residential connection against
https://www.mufap.com.pk/Industry/IndustryStatDaily?tab=1:

    httpx, minimal headers          403  (5,838 B challenge page)
    httpx, full Chrome header set   403  (3,479 B challenge page)
    httpx, tuned TLS cipher list    403  (5,838 B challenge page)
    curl (shell), same headers      200  (1,271,966 B)
    curl_cffi, chrome TLS profile   200  (1,272,021 B)

That measurement concluded the TLS handshake was the only discriminator. It was
taken from one network, and the first scheduled run on a GitHub-hosted runner
disproved the "source IP does not matter" half of it: the identical curl_cffi
request from an Azure datacenter address was refused three times. Cloudflare
scores TLS fingerprint *and* IP reputation, and a datacenter range starts from a
much worse prior than a residential one.

So the client now does what a browser does rather than only sounding like one:

  1. One session for the whole run, so cookies survive between requests. The
     `__cf_bm` cookie Cloudflare issues on a successful request is the thing
     that makes the *next* one cheap, and a fresh connection per request threw
     it away every time.
  2. A warm-up request to the site root before any data page. A real visitor
     arrives at the homepage and follows a link; landing cold and deep on a
     query-string report page is itself part of what gets scored.
  3. A `Referer` consistent with having done that.
  4. Several TLS profiles, tried in turn. A profile that a given Cloudflare
     deployment scores badly today may be fine tomorrow, and vice versa.
  5. Backoff measured in tens of seconds, with jitter. Cloudflare's rate
     heuristics are the one input here that retrying quickly actively worsens.

None of this defeats a decision to refuse; it removes the reasons to refuse
that are artefacts of being a script rather than of being unwelcome. If MUFAP
declines anyway the caller keeps serving its last-known-good snapshot marked
`degraded`, and the service stays up.

PSX needs none of this and stays on plain httpx.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re

from .config import get_settings
from .http import UpstreamError, fetch_text as fetch_text_httpx

logger = logging.getLogger(__name__)

# Tried in order until one is not refused. The first entry is whatever
# MUFAP_IMPERSONATE asks for; the rest are deliberately spread across engines,
# because a Cloudflare deployment that scores one Chrome build badly usually
# does not score Safari and Firefox the same way.
_FALLBACK_PROFILES = ("chrome131", "safari180", "firefox135", "chrome124")

# A Cloudflare challenge names its datacentre and request in an HTML comment or
# a footer; quoting it back is what makes a support conversation possible.
_RAY_ID = re.compile(r"[Rr]ay ID:?\s*</?[^>]*>?\s*([0-9a-f]{12,})")

_session = None
_session_profile: str | None = None
_warmed = False
_warned_missing = False


def _curl_requests():
    try:
        from curl_cffi import requests as curl_requests  # noqa: WPS433
    except ImportError:
        return None
    return curl_requests


def _profiles() -> list[str]:
    """The impersonation profiles to try, in order, without duplicates."""
    settings = get_settings()
    raw = os.getenv("MUFAP_IMPERSONATE_CHAIN", "").strip()
    if raw:
        candidates = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        candidates = [settings.mufap_impersonate or "chrome", *_FALLBACK_PROFILES]

    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _open_session(profile: str):
    """A fresh session bound to one TLS profile. Cookies live here."""
    global _session, _session_profile, _warmed

    curl_requests = _curl_requests()
    _close_session()
    _session = curl_requests.Session(impersonate=profile)
    _session_profile = profile
    _warmed = False
    return _session


def _close_session() -> None:
    global _session, _session_profile, _warmed
    if _session is not None:
        try:
            _session.close()
        except Exception:  # a session that will not close must not fail the run
            pass
    _session = None
    _session_profile = None
    _warmed = False


def close_browser_client() -> None:
    """Release the session. Called at shutdown alongside the httpx client."""
    _close_session()


def _describe(response) -> dict[str, object]:
    """What a refusal actually was, in terms worth logging."""
    body = ""
    try:
        body = response.text or ""
    except Exception:
        pass
    match = _RAY_ID.search(body)
    return {
        "status": response.status_code,
        "bytes": len(body),
        "cf_ray": response.headers.get("cf-ray") or (match.group(1) if match else None),
        "cf_mitigated": response.headers.get("cf-mitigated"),
        "server": response.headers.get("server"),
    }


async def _warm_up(session, timeout: float) -> None:
    """Land on the site root first, the way a visitor does.

    A failure here is not fatal. The warm-up exists to collect cookies; if it
    does not, the data request is simply no better off than it used to be.
    """
    global _warmed
    base = get_settings().mufap_base

    try:
        response = await asyncio.to_thread(
            session.get,
            f"{base}/",
            timeout=timeout,
            headers={
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        )
    except Exception as exc:
        logger.warning("mufap_warmup_error", extra={"error": type(exc).__name__})
        return

    # The names alone, never the values — and the log formatter redacts any
    # field whose key mentions cookies, so the diagnostic is expressed as the
    # one fact that matters: did Cloudflare hand us a clearance token.
    jar = set(session.cookies.keys())
    logger.info("mufap_warmup", extra={
        "status": response.status_code,
        "jar_size": len(jar),
        "cf_clearance_issued": bool(jar & {"__cf_bm", "cf_clearance"}),
    })
    _warmed = response.status_code == 200

    # A pause between the landing page and the report it links to. Two requests
    # in the same millisecond is not a browsing pattern.
    await asyncio.sleep(random.uniform(0.8, 1.8))


async def fetch_text_browser(url: str) -> str:
    """Fetch a page using a browser TLS profile, falling back to httpx.

    `MUFAP_HTTP_CLIENT=httpx` forces the plain client, for an environment where
    the extra dependency is unwanted or where access has been arranged directly.
    """
    global _warned_missing
    settings = get_settings()

    if settings.mufap_http_client == "httpx":
        return await fetch_text_httpx(url)

    if _curl_requests() is None:
        if not _warned_missing:
            logger.warning(
                "curl_cffi_unavailable",
                extra={"detail": "falling back to httpx; MUFAP will return 403"},
            )
            _warned_missing = True
        return await fetch_text_httpx(url)

    base = settings.mufap_base
    headers = {
        "Referer": f"{base}/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    last: dict[str, object] | None = None
    profiles = _profiles()

    for index, profile in enumerate(profiles):
        # Reuse the session across the run's two tab fetches; only build a new
        # one when moving to a different profile, since cookies are issued
        # against the fingerprint that earned them.
        session = _session if _session is not None and _session_profile == profile \
            else _open_session(profile)

        if index:
            # Escalating, jittered. Cloudflare's rate heuristics are the one
            # input that retrying quickly makes strictly worse.
            delay = min(settings.mufap_retry_backoff_s * (2 ** (index - 1)), 90.0)
            delay *= random.uniform(0.75, 1.25)
            logger.info("mufap_backoff", extra={"seconds": round(delay, 1),
                                                "next_profile": profile})
            await asyncio.sleep(delay)

        if not _warmed:
            await _warm_up(session, settings.http_timeout_s)

        try:
            response = await asyncio.to_thread(
                session.get, url, timeout=settings.http_timeout_s, headers=headers
            )
        except Exception as exc:
            logger.warning("mufap_fetch_error",
                           extra={"error": type(exc).__name__, "profile": profile})
            last = {"status": None, "error": type(exc).__name__}
            _close_session()
            continue

        if response.status_code == 200:
            if index:
                logger.info("mufap_profile_accepted", extra={"profile": profile,
                                                             "attempt": index + 1})
            return response.text

        last = {**_describe(response), "profile": profile}
        logger.warning("mufap_refused", extra=last)

        # 403 and 503 are the challenge responses. Anything else is a real HTTP
        # answer and trying a different TLS profile cannot change it.
        if response.status_code not in {403, 503, 429}:
            break
        _close_session()

    detail = ", ".join(f"{k}={v}" for k, v in (last or {}).items() if v is not None)
    raise UpstreamError(
        url,
        f"refused by upstream bot management after {len(profiles)} TLS profiles "
        f"({detail or 'no response'}). A datacenter source address is scored far "
        f"more harshly than a residential one; see docs/DEPLOYMENT.md.",
        (last or {}).get("status"),  # type: ignore[arg-type]
    )
