"""Vercel serverless entrypoint.

Vercel's Python runtime is serverless: process memory does not survive between
invocations and there is no place to run a background loop. Two consequences,
both handled here:

  1. The snapshot store must be external. SNAPSHOT_STORE=redis points it at
     Upstash over its REST API, which needs no persistent connection pool.

  2. Refreshes must be externally triggered. Vercel Cron calls
     GET /api/cron/refresh on the schedule in vercel.json.

Everything else — routers, validation, freshness — is the same code the Docker
deployment runs. This file only wires the serverless specifics.
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
from pathlib import Path

# The function's working directory is the repo root on Vercel; make the app
# package importable regardless of how the runtime invokes this module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Header, HTTPException, status  # noqa: E402

from app.main import create_app  # noqa: E402
from app.system import _refreshers  # noqa: E402

logger = logging.getLogger(__name__)

# Serverless has no background loop; Cron drives refreshes instead.
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("SNAPSHOT_STORE", "redis")
# Vercel serves the dashboard as static output, so the API does not mount it.
os.environ.setdefault("SERVE_STATIC", "false")

app = create_app(("psx", "mufap"), title="PK Finance Unified Service")


@app.get("/api/cron/refresh", include_in_schema=False)
async def cron_refresh(authorization: str | None = Header(default=None)):
    """Refresh every dataset. Called by Vercel Cron.

    Vercel sends `Authorization: Bearer $CRON_SECRET`. The secret is compared
    in constant time; without it configured the route refuses rather than
    running, since an open refresh endpoint is a way to drive unbounded traffic
    at dps.psx.com.pk and mufap.com.pk from this deployment's IP.
    """
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "CRON_SECRET is not configured.")

    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid cron credentials.")

    results: dict[str, object] = {}
    for name, refresher in _refreshers.items():
        try:
            snapshot = await refresher()
            results[name] = {"count": getattr(snapshot, "count", None),
                             "error": getattr(snapshot, "error", None)}
        except Exception as exc:
            logger.error("cron_refresh_failed", extra={"dataset": name, "error": str(exc)})
            results[name] = {"count": None, "error": "refresh failed"}

    return {"refreshed": results}
