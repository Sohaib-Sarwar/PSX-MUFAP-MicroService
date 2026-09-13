"""Background refresh loop for long-running deployments.

Adaptive rather than fixed-interval. The previous service scraped every 30
minutes around the clock: PSX trades roughly 31 of the 168 hours in a week, and
MUFAP strikes NAV once per business day, so about 81% of those requests fetched
data that had not changed.

Here:
  PSX    polls fast while the market is ticking and slowly when it is closed.
  MUFAP  polls hourly until the published validity date advances, then stops
         for the day.

Not used on Vercel — serverless has no background loop, and Vercel Cron calls
POST /internal/refresh instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable

from .infra.config import get_settings

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    def spawn(self, name: str, worker: Callable[[], Awaitable[float]]) -> None:
        """Run `worker` forever; it returns the delay before the next run."""
        self._tasks.append(asyncio.create_task(self._run(name, worker), name=name))

    async def _run(self, name: str, worker: Callable[[], Awaitable[float]]) -> None:
        # Stagger start-up so both domains do not hit their upstreams together.
        await self._sleep(1.0)
        while not self._stopping.is_set():
            delay = 60.0
            try:
                delay = await worker()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failing refresh must never kill the loop. The old code had no
                # try/except here, so one escaping exception silently stopped all
                # future refreshes while the service still reported healthy.
                logger.exception("scheduler_iteration_failed", extra={"loop": name})
            await self._sleep(max(delay, 5.0))

    async def _sleep(self, seconds: float) -> None:
        """Interruptible sleep so shutdown does not wait out a long interval."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        # Await cancellation so shutdown is graceful rather than abandoning
        # in-flight work — the old code called cancel() and never awaited it.
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("scheduler_stopped")


def build_psx_worker() -> Callable[[], Awaitable[float]]:
    from .psx import service as psx

    async def worker() -> float:
        s = get_settings()
        snapshot = await psx.refresh_stocks()
        await psx.refresh_indices()
        status = (snapshot.meta or {}).get("market_status")
        if status == "open":
            return s.psx_interval_open_s
        return s.psx_interval_closed_s

    return worker


def build_mufap_worker() -> Callable[[], Awaitable[float]]:
    from .mufap import service as mufap

    last_seen: dict[str, str | None] = {"validity": None}

    async def worker() -> float:
        s = get_settings()
        snapshot = await mufap.refresh_funds()
        validity = snapshot.data_as_of

        if validity and validity == last_seen["validity"]:
            # Nothing new published since the last run; there is no point
            # re-fetching a once-daily figure on a tight loop.
            logger.info("mufap_unchanged", extra={"validity_date": validity})
            return s.mufap_interval_s

        last_seen["validity"] = validity
        return s.mufap_interval_s

    return worker
