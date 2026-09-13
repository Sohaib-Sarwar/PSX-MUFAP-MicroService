"""MUFAP service: fetch both tabs, merge, validate, publish, read.

MUFAP strikes NAV once per business day. Polling faster than that cannot make
the number fresher, so the scheduler backs off once the validity date advances
rather than re-fetching on a fixed clock.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..infra.config import get_settings, now_pkt
from ..infra.browser_http import fetch_text_browser
from ..infra.metrics import metrics
from ..infra.store import Snapshot, get_store
from ..infra.validation import (
    apply_record_validation,
    fund_anomalies,
    gate_batch,
    validate_fund,
)
from . import parsers

logger = logging.getLogger(__name__)

DATASET_FUNDS = "mufap.funds"

_lock = asyncio.Lock()
_inflight: asyncio.Task | None = None


async def refresh_funds(force: bool = False) -> Snapshot:
    """Refresh the fund table, coalescing concurrent callers into one fetch."""
    global _inflight

    if _inflight and not _inflight.done():
        metrics.incr("refresh_coalesced_total", dataset=DATASET_FUNDS)
        return await _inflight

    async with _lock:
        if _inflight and not _inflight.done():
            return await _inflight
        _inflight = asyncio.create_task(_do_refresh())

    try:
        return await _inflight
    finally:
        _inflight = None


async def _do_refresh() -> Snapshot:
    s = get_settings()
    store = get_store()
    previous = await store.get(DATASET_FUNDS)
    fetched_at = now_pkt().isoformat()

    try:
        # Both tabs come from the same Cloudflare-fronted host. They are fetched
        # concurrently but share one connection pool, so this is two requests,
        # not two connections.
        # Sequential, not concurrent: two simultaneous requests to a
        # Cloudflare-fronted host is exactly the pattern that escalates a soft
        # challenge into a hard block. One after the other is polite and the
        # extra few seconds are irrelevant for a once-daily figure.
        returns_html = await fetch_text_browser(s.mufap_returns_url)
        prices_html = await fetch_text_browser(s.mufap_prices_url)
        returns_rows = parsers.parse_returns_tab(returns_html)
        price_rows = parsers.parse_prices_tab(prices_html)
        rows = parsers.merge_funds(returns_rows, price_rows)
        rows = apply_record_validation(DATASET_FUNDS, rows, validate_fund, fund_anomalies)
    except Exception as exc:
        logger.error("mufap_refresh_failed", exc_info=True, extra={"error": str(exc)})
        metrics.incr("refresh_failed_total", dataset=DATASET_FUNDS)
        return await _publish_failure(previous, str(exc), fetched_at)

    gate = gate_batch(
        DATASET_FUNDS, len(rows),
        previous.count if previous else None,
        min_rows=s.mufap_min_rows,
        max_drop_ratio=s.max_row_drop_ratio,
    )
    if not gate.accepted:
        return await _publish_failure(previous, f"batch rejected: {gate.reason}", fetched_at)

    snapshot = Snapshot(
        dataset=DATASET_FUNDS,
        rows=rows,
        meta={
            "categories": _category_counts(rows),
            "stats": _build_stats(rows),
            "amc_count": len({r["amc"] for r in rows if r.get("amc")}),
            "trustee_count": len({r["trustee"] for r in rows if r.get("trustee")}),
        },
        fetched_at=fetched_at,
        # Funds do not all publish on the same day, so the batch's "as of" is
        # the newest validity date present; each record keeps its own.
        data_as_of=parsers.latest_validity(rows),
    )
    await store.put(snapshot)
    metrics.incr("refresh_success_total", dataset=DATASET_FUNDS)
    metrics.gauge("snapshot_records", len(rows), dataset=DATASET_FUNDS)
    logger.info("mufap_refresh_ok",
                extra={"funds": len(rows), "as_of": snapshot.data_as_of})
    return snapshot


async def _publish_failure(previous: Snapshot | None, error: str,
                           fetched_at: str) -> Snapshot:
    store = get_store()
    if previous and previous.rows:
        degraded = Snapshot(
            dataset=DATASET_FUNDS, rows=previous.rows, meta=previous.meta,
            fetched_at=previous.fetched_at, data_as_of=previous.data_as_of,
            error=error[:300],
        )
        await store.put(degraded)
        return degraded

    empty = Snapshot(dataset=DATASET_FUNDS, rows=[], meta={},
                     fetched_at=fetched_at, error=error[:300])
    await store.put(empty)
    return empty


def _category_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        category = row.get("category") or "Uncategorised"
        counts[category] = counts.get(category, 0) + 1
    return [{"category": k, "count": v} for k, v in sorted(counts.items())]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _build_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    navs = [r["nav"] for r in rows if r.get("nav") is not None]
    ytd = [r["returns"]["ytd"] for r in rows
           if r.get("returns") and r["returns"].get("ytd") is not None]
    return {
        "total_funds": len(rows),
        "total_categories": len({r.get("category") for r in rows if r.get("category")}),
        "nav": {
            "mean": round(sum(navs) / len(navs), 4) if navs else None,
            "median": round(_median(navs), 4) if navs else None,
            "min": round(min(navs), 4) if navs else None,
            "max": round(max(navs), 4) if navs else None,
        },
        "ytd_return": {
            "mean": round(sum(ytd) / len(ytd), 2) if ytd else None,
            "best": round(max(ytd), 2) if ytd else None,
            "worst": round(min(ytd), 2) if ytd else None,
            "reported_by": len(ytd),
        },
    }


async def get_funds_snapshot() -> Snapshot:
    return await get_store().get(DATASET_FUNDS) or Snapshot(dataset=DATASET_FUNDS)
