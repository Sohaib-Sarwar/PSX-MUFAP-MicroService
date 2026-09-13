"""Application factory.

One factory serves all three branches. `domains` decides which routers are
mounted; the split branches ship only the package they need, so an unused
domain is absent from the image rather than merely unrouted.

Startup does NOT block on a scrape. The previous service ran both scrapes
inside lifespan before yielding, so with slow upstreams the app bound no
traffic for up to ~350s against a 90s healthcheck grace period — long enough
for an orchestrator to mark it unhealthy and restart it, forever. Here the app
serves immediately, /ready reports 503 until data lands, and the first refresh
runs in the background.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Iterable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from . import system
from .infra.config import get_settings
from .infra.errors import install_error_handlers
from .infra.http import close_client
from .infra.logging_setup import configure_logging
from .infra.security import RateLimitMiddleware, RequestContextMiddleware
from .infra.store import reset_store

logger = logging.getLogger(__name__)

VERSION = "6.0.0"


def _register_mufap() -> None:
    from .mufap import service as mufap

    async def probe():
        snapshot = await mufap.get_funds_snapshot()
        return {
            "ready": snapshot.count > 0,
            "records": snapshot.count,
            "state": snapshot.freshness(get_settings().mufap_stale_after_s)["state"],
            "data_as_of": snapshot.data_as_of,
        }

    return system.register_dataset("mufap", probe, mufap.refresh_funds)


def create_app(domains: Iterable[str] = ("psx", "mufap"), *,
               title: str | None = None) -> FastAPI:
    configure_logging()
    settings = get_settings()
    domains = tuple(domains)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "service_starting",
            extra={"domains": list(domains), "store": settings.snapshot_store,
                   "scheduler": settings.scheduler_enabled, "version": VERSION},
        )

        scheduler = None
        if settings.scheduler_enabled:
            from .scheduler import Scheduler, build_mufap_worker

            scheduler = Scheduler()
            if "mufap" in domains:
                scheduler.spawn("mufap", build_mufap_worker())
            app.state.scheduler = scheduler
        else:
            logger.info("scheduler_disabled",
                        extra={"reason": "serverless — refresh via POST /internal/refresh"})

        # The app is already accepting traffic at this point.
        yield

        if scheduler is not None:
            await scheduler.stop()
        await close_client()
        await reset_store()
        logger.info("service_stopped")

    app = FastAPI(
        title=title or "PK Finance Service",
        description=_description(domains),
        version=VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(GZipMiddleware, minimum_size=800)
    app.add_middleware(RateLimitMiddleware, limit_per_minute=settings.rate_limit_per_minute)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # A wildcard origin with credentials is contradictory — browsers refuse
        # to send credentials to "*" anyway. Credentials stay off unless an
        # explicit origin list is configured.
        allow_credentials=(
            settings.cors_allow_credentials and settings.cors_origins != ["*"]
        ),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    install_error_handlers(app)
    app.include_router(system.router)

    if "mufap" in domains:
        from .mufap.router import router as mufap_router

        _register_mufap()
        app.include_router(mufap_router)

    @app.get("/api", tags=["System"], summary="API index")
    async def api_index():
        return {
            "service": app.title,
            "version": VERSION,
            "domains": list(domains),
            "datasets": system.registered_datasets(),
            "docs": "/docs",
        }

    return app


def _description(domains: tuple[str, ...]) -> str:
    parts = ["Pakistan financial market data."]
    if "psx" in domains:
        parts.append("- **PSX Stock Exchange** — `/api/psx/...`")
    if "mufap" in domains:
        parts.append("- **MUFAP Mutual Funds** — `/api/mufap/...`")
    parts.append("\nEvery response carries a `freshness` block: "
                 "`fresh` | `stale` | `degraded` | `unavailable`.")
    return "\n\n".join(parts)


# Default app: both domains plus the dashboard. The split branches override this.
app = create_app(
    domains=("mufap",),
    title="MUFAP Mutual Funds Service",
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
