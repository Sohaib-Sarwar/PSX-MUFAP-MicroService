"""Operational endpoints: liveness, readiness, metrics, and the Cron hook.

/health and /ready are deliberately different things:

  /health  is the process alive? Returns 200 as soon as the app is serving,
           regardless of data. This is what a container healthcheck and a
           restart policy should watch — tying it to data means an upstream
           outage triggers a restart loop that cannot possibly help.

  /ready   can this instance serve useful answers? Returns 503 until at least
           one dataset has data. This is what a load balancer should watch.

The previous service had one endpoint that returned 200 "healthy" even with
zero records loaded, so a total data failure looked identical to normal
operation to every monitor pointed at it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from fastapi import APIRouter, Depends, Response

from .infra.config import get_settings, now_pkt
from .infra.metrics import metrics
from .infra.security import require_internal_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["System"])

# Registered by create_app so this module does not import the domain packages
# (the split branches ship only one of them).
_probes: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {}
_refreshers: dict[str, Callable[[], Awaitable[Any]]] = {}


def register_dataset(
    name: str,
    probe: Callable[[], Awaitable[dict[str, Any]]],
    refresher: Callable[[], Awaitable[Any]],
) -> None:
    _probes[name] = probe
    _refreshers[name] = refresher


def registered_datasets() -> list[str]:
    return sorted(_probes)


@router.get("/health", summary="Liveness — is the process up?")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": get_settings().service_name,
        "time": now_pkt().isoformat(),
    }


@router.get("/ready", summary="Readiness — can this instance answer usefully?")
async def ready(response: Response) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    any_ready = False

    for name, probe in _probes.items():
        try:
            info = await probe()
        except Exception as exc:  # a probe must never take the endpoint down
            logger.error("probe_failed", extra={"dataset": name, "error": str(exc)})
            info = {"ready": False, "error": "probe failed"}
        datasets[name] = info
        any_ready = any_ready or bool(info.get("ready"))

    if not any_ready:
        response.status_code = 503

    return {
        "status": "ready" if any_ready else "warming_up",
        "datasets": datasets,
        "time": now_pkt().isoformat(),
    }


@router.get("/metrics", summary="Prometheus metrics")
async def prometheus() -> Response:
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")


@router.post(
    "/internal/refresh",
    dependencies=[Depends(require_internal_token)],
    summary="Refresh every dataset (Vercel Cron target)",
)
async def refresh_all() -> dict[str, Any]:
    """Refresh hook for serverless deployments.

    On Vercel there is no background loop, so Vercel Cron calls this on a
    schedule. Guarded by X-Internal-Token: an open refresh endpoint is a way to
    drive unbounded traffic at the upstream sites from this service's IP.
    """
    results: dict[str, Any] = {}
    for name, refresher in _refreshers.items():
        try:
            snapshot = await refresher()
            results[name] = {"count": getattr(snapshot, "count", None),
                             "error": getattr(snapshot, "error", None)}
        except Exception as exc:
            logger.error("refresh_failed", extra={"dataset": name, "error": str(exc)})
            results[name] = {"count": None, "error": "refresh failed"}
    return {"refreshed": results, "time": now_pkt().isoformat()}
