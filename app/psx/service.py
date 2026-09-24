"""PSX service: fetch, validate, publish, read.

Refreshes are coalesced — concurrent callers share one upstream request instead
of each starting their own. This matters because the refresh endpoint is
reachable from outside; without coalescing a burst of requests becomes a burst
of traffic at dps.psx.com.pk.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..infra.config import get_settings, now_pkt
from ..infra.http import fetch_text
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
DATASET_SESSION = "psx.session"

# One lock per dataset: a second refresh awaits the first rather than duplicating it.
_locks: dict[str, asyncio.Lock] = {}
_inflight: dict[str, asyncio.Task] = {}


def _lock(dataset: str) -> asyncio.Lock:
    if dataset not in _locks:
        _locks[dataset] = asyncio.Lock()
    return _locks[dataset]


# ── market status ─────────────────────────────────────────────────────────────

async def derive_market_status() -> dict[str, Any]:
    """Whether PSX is trading, taken from the board rather than inferred.

    This used to be deduced from the age of the newest KSE100 intraday tick,
    because nothing published the state directly. /trading-panel does: it names
    every market segment and whether it is Open or Closed, so there is no
    window to tune, no clock arithmetic, and nothing to get wrong on a public
    holiday or over Ramadan hours.
    """
    s = get_settings()
    try:
        panel = parsers.parse_trading_panel(await fetch_text(s.psx_trading_panel_url))
    except Exception as exc:
        logger.warning("market_status_unknown", extra={"error": str(exc)})
        return {"status": "unknown", "session_at": None, "segments": []}

    return {
        "status": panel.get("market_status", "unknown"),
        "session_at": panel.get("session_at"),
        "segments": panel.get("segments", []),
    }


# ── quote enrichment ──────────────────────────────────────────────────────────
# The screener carries price and fundamentals for every instrument but no OHLC
# and no session volume; those survive only on each instrument's own page. One
# request per symbol is not a bulk feed, so a bounded number of the largest
# instruments are enriched per run and the rest report `has_quote: false`.

async def _fetch_details(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch full quotes for a bounded list of instruments, one page each.

    Sequential and paced. This is the one place the service makes more than a
    handful of requests, and it is capped by PSX_DETAIL_BUDGET precisely so the
    cost of a run stays a known, small number rather than a function of how many
    instruments PSX happens to list.

    Every failure is swallowed per symbol: a quote that cannot be fetched leaves
    its fields null, which the envelope already reports honestly. One unreachable
    page must not cost the other 746 instruments their refresh.
    """
    s = get_settings()
    out: dict[str, dict[str, Any]] = {}
    if not symbols:
        return out

    for index, symbol in enumerate(symbols):
        if index:
            await asyncio.sleep(s.psx_detail_pause_s)
        try:
            html = await fetch_text(s.psx_company_url(symbol))
            detail = parsers.parse_company(html, symbol)
        except Exception as exc:
            logger.debug("psx_detail_failed", extra={"symbol": symbol,
                                                     "error": type(exc).__name__})
            continue
        if detail.get("volume") is not None or detail.get("name"):
            out[symbol] = detail

    metrics.gauge("psx_details_fetched", len(out))
    logger.info("psx_details_fetched", extra={"requested": len(symbols),
                                              "resolved": len(out)})
    return out


def _detail_targets(rows: list[dict[str, Any]], budget: int) -> list[str]:
    """Which instruments to spend this run's quote budget on.

    Largest by market capitalisation. That is where a missing volume is most
    noticed, and it covers the index constituents without this service needing
    to track which instruments those are.
    """
    if budget <= 0:
        return []
    ranked = sorted(rows, key=lambda r: r.get("market_cap") or 0, reverse=True)
    return [r["symbol"] for r in ranked[:budget]]


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
        # Same origin, two independent pages, so one round trip rather than two.
        screener_html, panel_html = await asyncio.gather(
            fetch_text(s.psx_screener_url),
            fetch_text(s.psx_trading_panel_url),
        )
        universe = parsers.parse_screener(screener_html)
        del screener_html
        session = parsers.parse_trading_panel(panel_html)
        del panel_html

        details = await _fetch_details(_detail_targets(universe, s.psx_detail_budget))
        inherited = {r["symbol"]: r for r in ((previous.rows if previous else []) or [])
                     if r.get("symbol")}
        rows = parsers.merge_universe(universe, inherited, details)
        rows = apply_record_validation(DATASET_STOCKS, rows, validate_quote,
                                       quote_anomalies)
    except Exception as exc:
        logger.error("psx_refresh_failed", exc_info=True, extra={"error": str(exc)})
        metrics.incr("refresh_failed_total", dataset=DATASET_STOCKS)
        return await _publish_failure(DATASET_STOCKS, previous, str(exc), fetched_at)

    # The gate counts the whole universe now. It used to count only the rows
    # that traded, which was the right measure when the board listed exactly
    # those; the screener lists everything, so a collapse shows up as rows
    # disappearing from it.
    gate = gate_batch(
        DATASET_STOCKS,
        len(rows),
        (previous.meta or {}).get("listed_count") if previous else None,
        min_rows=s.psx_min_rows,
        max_drop_ratio=s.max_row_drop_ratio,
    )
    if not gate.accepted:
        return await _publish_failure(
            DATASET_STOCKS, previous, f"batch rejected: {gate.reason}", fetched_at
        )

    await _publish_session(session, fetched_at)

    snapshot = Snapshot(
        dataset=DATASET_STOCKS,
        rows=rows,
        meta={
            "listed_count": len(rows),
            "traded_count": session.get("traded_instruments"),
            "quoted_count": sum(1 for r in rows if r.get("has_quote")),
            "named_count": sum(1 for r in rows if r.get("name")),
            "market_status": session.get("market_status"),
            "summary": _build_summary(rows, session),
        },
        fetched_at=fetched_at,
        # PSX's own stamp for the session, not ours for the fetch.
        data_as_of=session.get("session_at"),
    )
    await store.put(snapshot)
    metrics.incr("refresh_success_total", dataset=DATASET_STOCKS)
    metrics.gauge("snapshot_records", len(rows), dataset=DATASET_STOCKS)
    logger.info("psx_refresh_ok",
                extra={"listed": len(rows), "quoted": snapshot.meta["quoted_count"],
                       "market": session.get("market_status"),
                       "session_at": session.get("session_at")})
    return snapshot


async def _publish_session(session: dict[str, Any], fetched_at: str) -> None:
    """The session totals, kept as their own dataset.

    They describe the market rather than any instrument, and they are the one
    part of a PSX refresh that is authoritative rather than derived, so they get
    their own snapshot instead of being buried in the stock metadata.
    """
    await get_store().put(Snapshot(
        dataset=DATASET_SESSION,
        rows=session.get("segments", []),
        meta={k: v for k, v in session.items() if k != "segments"},
        fetched_at=fetched_at,
        data_as_of=session.get("session_at"),
    ))


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


def _build_summary(rows: list[dict], session: dict[str, Any]) -> dict[str, Any]:
    """Market breadth, taken from PSX rather than counted here.

    Breadth used to be computed by counting the board, which worked because the
    board held exactly the instruments that traded. The screener holds every
    listed instrument and reports the last change for all of them, so counting
    it would score a stock that did not trade today as "unchanged" and inflate
    every figure. /trading-panel publishes the real counts, so they are taken
    from there and the derived figures are clearly named as derived.
    """
    changes = [r["change_pct"] for r in rows if r.get("change_pct") is not None]
    quoted = [r for r in rows if r.get("has_quote")]
    caps = [r["market_cap"] for r in rows if r.get("market_cap")]

    return {
        "listed_instruments": len(rows),
        # Authoritative, from the exchange's own session header.
        "traded_instruments": session.get("traded_instruments"),
        "gainers": session.get("advancing"),
        "losers": session.get("declining"),
        "unchanged": session.get("unchanged"),
        "total_trades": session.get("total_trades"),
        "total_volume": session.get("total_volume"),
        "total_traded_value": session.get("total_traded_value"),
        "market_capitalisation": round(sum(caps), 2) if caps else None,
        # Derived across the listed universe, not the traded subset.
        "avg_change_pct": round(sum(changes) / len(changes), 2) if changes else None,
        "instruments_with_full_quote": len(quoted),
        "session_at": session.get("session_at"),
        "market_status": session.get("market_status"),
    }


# ── read helpers ──────────────────────────────────────────────────────────────

async def get_stocks_snapshot() -> Snapshot:
    return await get_store().get(DATASET_STOCKS) or Snapshot(dataset=DATASET_STOCKS)


async def get_indices_snapshot() -> Snapshot:
    return await get_store().get(DATASET_INDICES) or Snapshot(dataset=DATASET_INDICES)


async def get_session_snapshot() -> Snapshot:
    return await get_store().get(DATASET_SESSION) or Snapshot(dataset=DATASET_SESSION)
