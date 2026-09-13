"""PSX service: fetch, validate, publish, read.

Refreshes are coalesced — concurrent callers share one upstream request instead
of each starting their own. This matters because the refresh endpoint is
reachable from outside; without coalescing a burst of requests becomes a burst
of traffic at dps.psx.com.pk.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from ..infra.config import PKT, get_settings, now_pkt
from ..infra.http import fetch_json, fetch_text
from ..infra.metrics import metrics
from ..infra.store import Snapshot, get_store
from ..infra.validation import (
    apply_record_validation,
    gate_batch,
    quote_anomalies,
    validate_quote,
)
from . import parsers

logger = logging.getLogger(__name__)

DATASET_STOCKS = "psx.stocks"
DATASET_INDICES = "psx.indices"

# One lock per dataset: a second refresh awaits the first rather than duplicating it.
_locks: dict[str, asyncio.Lock] = {}
_inflight: dict[str, asyncio.Task] = {}


def _lock(dataset: str) -> asyncio.Lock:
    if dataset not in _locks:
        _locks[dataset] = asyncio.Lock()
    return _locks[dataset]


# ── market status ─────────────────────────────────────────────────────────────

async def derive_market_status() -> dict[str, Any]:
    """Infer whether PSX is trading from the index feed itself.

    The newest tick in the KSE100 intraday series says when the market last
    moved. If that is minutes old the market is open; if it is days old it is
    closed. This needs no trading calendar and stays correct across weekends,
    public holidays and Ramadan hours on its own — PSX's /calendar page is an
    AGM calendar and does not carry trading days.
    """
    s = get_settings()
    try:
        payload = await fetch_json(s.psx_timeseries_url("int", "KSE100"))
        points = parsers.parse_timeseries(payload, "int")
    except Exception as exc:
        logger.warning("market_status_unknown", extra={"error": str(exc)})
        return {"status": "unknown", "last_tick": None, "seconds_since_tick": None}

    if not points:
        return {"status": "unknown", "last_tick": None, "seconds_since_tick": None}

    last_dt = datetime.fromtimestamp(points[-1]["timestamp"], tz=PKT)
    age = (now_pkt() - last_dt).total_seconds()
    return {
        "status": "open" if age <= s.market_open_tick_window_s else "closed",
        "last_tick": last_dt.isoformat(),
        "seconds_since_tick": round(age),
    }


# ── refresh ───────────────────────────────────────────────────────────────────

async def refresh_stocks(force: bool = False) -> Snapshot:
    """Fetch the listed universe and the session board, merge, validate, publish."""
    return await _coalesced(DATASET_STOCKS, _do_refresh_stocks)


async def refresh_indices(force: bool = False) -> Snapshot:
    return await _coalesced(DATASET_INDICES, _do_refresh_indices)


async def _coalesced(dataset: str, worker) -> Snapshot:
    existing = _inflight.get(dataset)
    if existing and not existing.done():
        metrics.incr("refresh_coalesced_total", dataset=dataset)
        return await existing

    async with _lock(dataset):
        existing = _inflight.get(dataset)
        if existing and not existing.done():
            return await existing
        task = asyncio.create_task(worker())
        _inflight[dataset] = task

    try:
        return await task
    finally:
        _inflight.pop(dataset, None)


async def _do_refresh_stocks() -> Snapshot:
    s = get_settings()
    store = get_store()
    previous = await store.get(DATASET_STOCKS)
    fetched_at = now_pkt().isoformat()

    try:
        # Two different hosts' paths but the same origin, so fetch concurrently.
        symbols_payload, market_html, status = await asyncio.gather(
            fetch_json(s.psx_symbols_url),
            fetch_text(s.psx_market_watch_url),
            derive_market_status(),
        )
        symbols = parsers.parse_symbols(symbols_payload)
        quotes = parsers.parse_market_watch(market_html)
        quotes = apply_record_validation(DATASET_STOCKS, quotes, validate_quote,
                                         quote_anomalies)
        rows = parsers.merge_universe(symbols, quotes)
    except Exception as exc:
        logger.error("psx_refresh_failed", exc_info=True, extra={"error": str(exc)})
        metrics.incr("refresh_failed_total", dataset=DATASET_STOCKS)
        return await _publish_failure(DATASET_STOCKS, previous, str(exc), fetched_at)

    traded = [r for r in rows if r.get("traded")]
    gate = gate_batch(
        DATASET_STOCKS,
        len(traded),
        (previous.meta or {}).get("traded_count") if previous else None,
        min_rows=s.psx_min_rows,
        max_drop_ratio=s.max_row_drop_ratio,
    )
    if not gate.accepted:
        return await _publish_failure(
            DATASET_STOCKS, previous, f"batch rejected: {gate.reason}", fetched_at
        )

    snapshot = Snapshot(
        dataset=DATASET_STOCKS,
        rows=rows,
        meta={
            "traded_count": len(traded),
            "listed_count": len(rows),
            "market_status": status.get("status"),
            "summary": _build_summary(traded, len(rows)),
        },
        fetched_at=fetched_at,
        data_as_of=status.get("last_tick"),
    )
    await store.put(snapshot)
    metrics.incr("refresh_success_total", dataset=DATASET_STOCKS)
    metrics.gauge("snapshot_records", len(rows), dataset=DATASET_STOCKS)
    logger.info(
        "psx_refresh_ok",
        extra={"listed": len(rows), "traded": len(traded), "market": status.get("status")},
    )
    return snapshot


async def _do_refresh_indices() -> Snapshot:
    s = get_settings()
    store = get_store()
    previous = await store.get(DATASET_INDICES)
    fetched_at = now_pkt().isoformat()

    try:
        html = await fetch_text(s.psx_indices_url)
        rows = parsers.parse_indices(html)
    except Exception as exc:
        logger.error("psx_indices_refresh_failed", exc_info=True, extra={"error": str(exc)})
        metrics.incr("refresh_failed_total", dataset=DATASET_INDICES)
        return await _publish_failure(DATASET_INDICES, previous, str(exc), fetched_at)

    gate = gate_batch(
        DATASET_INDICES, len(rows),
        previous.count if previous else None,
        min_rows=1, max_drop_ratio=s.max_row_drop_ratio,
    )
    if not gate.accepted:
        return await _publish_failure(
            DATASET_INDICES, previous, f"batch rejected: {gate.reason}", fetched_at
        )

    snapshot = Snapshot(
        dataset=DATASET_INDICES, rows=rows, meta={},
        fetched_at=fetched_at,
        data_as_of=(previous.data_as_of if previous else None),
    )
    await store.put(snapshot)
    metrics.incr("refresh_success_total", dataset=DATASET_INDICES)
    metrics.gauge("snapshot_records", len(rows), dataset=DATASET_INDICES)
    return snapshot


async def _publish_failure(dataset: str, previous: Snapshot | None,
                           error: str, fetched_at: str) -> Snapshot:
    """Keep serving the last good data, but say plainly that it is degraded."""
    store = get_store()
    if previous and previous.rows:
        degraded = Snapshot(
            dataset=dataset, rows=previous.rows, meta=previous.meta,
            fetched_at=previous.fetched_at, data_as_of=previous.data_as_of,
            error=error[:300],
        )
        await store.put(degraded)
        return degraded

    empty = Snapshot(dataset=dataset, rows=[], meta={}, fetched_at=fetched_at,
                     data_as_of=None, error=error[:300])
    await store.put(empty)
    return empty


def _build_summary(traded: list[dict], listed_total: int) -> dict[str, Any]:
    gainers = sum(1 for r in traded if (r.get("change") or 0) > 0)
    losers = sum(1 for r in traded if (r.get("change") or 0) < 0)
    volume = sum(r.get("volume") or 0 for r in traded)
    value = sum((r.get("current") or 0) * (r.get("volume") or 0) for r in traded)
    changes = [r["change_pct"] for r in traded if r.get("change_pct") is not None]
    return {
        "listed_instruments": listed_total,
        "traded_instruments": len(traded),
        "gainers": gainers,
        "losers": losers,
        "unchanged": len(traded) - gainers - losers,
        "total_volume": volume,
        "total_traded_value": round(value, 2),
        "avg_change_pct": round(sum(changes) / len(changes), 2) if changes else None,
    }


# ── read helpers ──────────────────────────────────────────────────────────────

async def get_stocks_snapshot() -> Snapshot:
    return await get_store().get(DATASET_STOCKS) or Snapshot(dataset=DATASET_STOCKS)


async def get_indices_snapshot() -> Snapshot:
    return await get_store().get(DATASET_INDICES) or Snapshot(dataset=DATASET_INDICES)


async def get_timeseries(symbol: str, kind: str) -> list[dict[str, Any]]:
    s = get_settings()
    payload = await fetch_json(s.psx_timeseries_url(kind, symbol.upper()))
    return parsers.parse_timeseries(payload, kind)
