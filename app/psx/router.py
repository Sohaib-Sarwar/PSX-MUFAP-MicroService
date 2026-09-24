"""PSX HTTP API — /api/psx/*

Route order matters in FastAPI: every literal path is declared before
/stocks/{symbol} so the literals are not swallowed by the path parameter.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from ..infra.config import get_settings
from ..infra.errors import NotFound
from ..infra.responses import (
    apply_filters,
    contains,
    envelope,
    paginate,
    require_rows,
    round_money,
    single,
    sort_rows,
)
from ..infra.security import require_internal_token
from . import service

router = APIRouter(prefix="/api/psx", tags=["PSX Stock Exchange"])

SORTABLE = (
    "symbol", "name", "sector", "ldcp", "open", "high", "low",
    "current", "change", "change_pct", "volume",
    # Published by /screener since PSX withdrew the bulk quote feed.
    "change_1y_pct", "market_cap", "pe_ratio", "dividend_yield",
    "free_float", "volume_30d_avg",
)

_PRICE_FIELDS = ("ldcp", "open", "high", "low", "current", "change")


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    """Round money at the boundary; keep exact values internally."""
    out = dict(row)
    for field in _PRICE_FIELDS:
        out[field] = round_money(out.get(field), 2)
    out["change_pct"] = round_money(out.get("change_pct"), 2)
    return out


def _stale_after() -> int:
    return get_settings().psx_stale_after_s


@router.get("/", summary="PSX service info")
async def psx_root() -> dict[str, Any]:
    snapshot = await service.get_stocks_snapshot()
    return {
        "service": "PSX Stock Exchange",
        "datasets": {
            "stocks": snapshot.count,
            "indices": (await service.get_indices_snapshot()).count,
        },
        "freshness": snapshot.freshness(_stale_after(),
                                        (snapshot.meta or {}).get("market_status")),
    }


@router.get("/market-status", summary="Whether PSX is currently trading")
async def market_status() -> dict[str, Any]:
    return await service.derive_market_status()


@router.get("/session", summary="Session totals and per-market segment states")
async def session() -> dict[str, Any]:
    snapshot = await service.get_session_snapshot()
    return {
        **(snapshot.meta or {}),
        "segments": snapshot.rows,
        "freshness": snapshot.freshness(_stale_after()),
    }


@router.get("/stocks", summary="All listed instruments, filtered and paginated")
async def list_stocks(
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("volume"),
    ascending: bool = Query(False),
    search: Optional[str] = Query(None, description="Match symbol or company name"),
    sector: Optional[str] = Query(None),
    traded_only: bool = Query(True, description="Exclude instruments that did not trade"),
    is_etf: Optional[bool] = Query(None),
    is_debt: Optional[bool] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    min_volume: Optional[int] = Query(None, ge=0),
    min_change_pct: Optional[float] = Query(None),
    max_change_pct: Optional[float] = Query(None),
) -> dict[str, Any]:
    snapshot = await service.get_stocks_snapshot()
    rows = require_rows(snapshot, "PSX stock data")

    filters = []
    if traded_only:
        filters.append(lambda r: r.get("traded"))
    if search:
        filters.append(lambda r: contains(r.get("symbol"), search) or contains(r.get("name"), search))
    if sector:
        filters.append(lambda r: contains(r.get("sector"), sector))
    if is_etf is not None:
        filters.append(lambda r: bool(r.get("is_etf")) is is_etf)
    if is_debt is not None:
        filters.append(lambda r: bool(r.get("is_debt")) is is_debt)
    if min_price is not None:
        filters.append(lambda r: r.get("current") is not None and r["current"] >= min_price)
    if max_price is not None:
        filters.append(lambda r: r.get("current") is not None and r["current"] <= max_price)
    if min_volume is not None:
        filters.append(lambda r: (r.get("volume") or 0) >= min_volume)
    if min_change_pct is not None:
        filters.append(lambda r: r.get("change_pct") is not None and r["change_pct"] >= min_change_pct)
    if max_change_pct is not None:
        filters.append(lambda r: r.get("change_pct") is not None and r["change_pct"] <= max_change_pct)

    filtered = apply_filters(rows, filters)
    ordered = sort_rows(filtered, sort_by, ascending, SORTABLE)
    page = [_shape(r) for r in paginate(ordered, offset, limit)]

    return envelope(
        page, snapshot=snapshot, stale_after_s=_stale_after(),
        total=len(filtered), offset=offset, limit=limit,
        market_status=(snapshot.meta or {}).get("market_status"),
    )


@router.get("/stocks/search", summary="Search by symbol or company name")
async def search_stocks(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    snapshot = await service.get_stocks_snapshot()
    rows = require_rows(snapshot, "PSX stock data")
    matches = [r for r in rows
               if contains(r.get("symbol"), q) or contains(r.get("name"), q)]
    # Exact ticker first, then traded instruments, then the rest.
    matches.sort(key=lambda r: (r.get("symbol", "").upper() != q.upper(),
                                not r.get("traded"),
                                r.get("symbol", "")))
    page = [_shape(r) for r in matches[:limit]]
    return envelope(page, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(matches), offset=0, limit=limit,
                    extra={"query": q})


@router.get("/stocks/gainers", summary="Top gainers by percentage change")
async def gainers(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return await _movers(limit, gaining=True)


@router.get("/stocks/losers", summary="Top losers by percentage change")
async def losers(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return await _movers(limit, gaining=False)


async def _movers(limit: int, *, gaining: bool) -> dict[str, Any]:
    snapshot = await service.get_stocks_snapshot()
    rows = require_rows(snapshot, "PSX stock data")
    pool = [r for r in rows
            if r.get("traded") and r.get("change_pct") is not None
            and ((r["change_pct"] > 0) if gaining else (r["change_pct"] < 0))]
    pool.sort(key=lambda r: r["change_pct"], reverse=gaining)
    page = [_shape(r) for r in pool[:limit]]
    return envelope(page, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(pool), offset=0, limit=limit,
                    market_status=(snapshot.meta or {}).get("market_status"))


@router.get("/stocks/active", summary="Most active by traded volume")
async def most_active(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    snapshot = await service.get_stocks_snapshot()
    rows = require_rows(snapshot, "PSX stock data")
    pool = [r for r in rows if r.get("traded")]
    pool.sort(key=lambda r: r.get("volume") or 0, reverse=True)
    page = [_shape(r) for r in pool[:limit]]
    return envelope(page, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(pool), offset=0, limit=limit)


@router.get("/stocks/summary", summary="Market breadth and totals")
async def summary() -> dict[str, Any]:
    snapshot = await service.get_stocks_snapshot()
    require_rows(snapshot, "PSX stock data")
    return {
        **(snapshot.meta or {}).get("summary", {}),
        "freshness": snapshot.freshness(_stale_after(),
                                        (snapshot.meta or {}).get("market_status")),
    }


@router.get("/stocks/{symbol}", summary="One instrument")
async def stock_detail(symbol: str) -> dict[str, Any]:
    snapshot = await service.get_stocks_snapshot()
    rows = require_rows(snapshot, "PSX stock data")
    target = symbol.upper()
    match = next((r for r in rows if r.get("symbol", "").upper() == target), None)
    if match is None:
        raise NotFound(f"Symbol '{target}' is not listed on PSX.")
    return single(_shape(match), snapshot=snapshot, stale_after_s=_stale_after(),
                  market_status=(snapshot.meta or {}).get("market_status"))


@router.get("/indices", summary="Index board (KSE100, KSE30, KMI30, …)")
async def list_indices() -> dict[str, Any]:
    snapshot = await service.get_indices_snapshot()
    rows = require_rows(snapshot, "PSX index data")
    shaped = [
        {**r,
         "value": round_money(r.get("value"), 2),
         "current": round_money(r.get("current"), 2),
         "high": round_money(r.get("high"), 2),
         "low": round_money(r.get("low"), 2),
         "change": round_money(r.get("change"), 2),
         "change_pct": round_money(r.get("change_pct"), 2)}
        for r in rows
    ]
    return envelope(shaped, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(shaped), offset=0, limit=None)


# ── internal: refresh ─────────────────────────────────────────────────────────
# Guarded by X-Internal-Token. Left unauthenticated, these are a way to drive
# unbounded traffic at dps.psx.com.pk from this service's IP address.

@router.post("/refresh", dependencies=[Depends(require_internal_token)],
             summary="Force a PSX refresh (authenticated)")
async def refresh() -> dict[str, Any]:
    stocks = await service.refresh_stocks(force=True)
    indices = await service.refresh_indices(force=True)
    return {
        "stocks": {"count": stocks.count, "error": stocks.error},
        "indices": {"count": indices.count, "error": indices.error},
    }
