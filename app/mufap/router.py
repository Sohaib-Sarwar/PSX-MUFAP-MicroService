"""MUFAP HTTP API — /api/mufap/*

Literal paths are declared before /funds/{...} so route order cannot shadow them.
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
from .parsers import RETURN_PERIODS

router = APIRouter(prefix="/api/mufap", tags=["MUFAP Mutual Funds"])

SORTABLE = (
    "fund_name", "category", "amc", "trustee", "rating", "nav",
    "offer_price", "repurchase_price", "validity_date",
    "return_ytd", "return_mtd", "return_d365", "return_y3",
)

_RETURN_SORT = {f"return_{key}": key for key, _ in RETURN_PERIODS}


def _stale_after() -> int:
    return get_settings().mufap_stale_after_s


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    """Round to the precision MUFAP publishes: 4dp for NAV and prices, 2dp returns."""
    out = dict(row)
    for field in ("nav", "offer_price", "repurchase_price", "market_price"):
        out[field] = round_money(out.get(field), 4)
    for field in ("front_end_load", "back_end_load", "contingent_load"):
        out[field] = round_money(out.get(field), 4)
    returns = out.get("returns") or {}
    out["returns"] = {k: round_money(v, 2) for k, v in returns.items()}
    return out


def _flatten_for_sort(rows: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    """Expose returns.<period> as a top-level key so sorting can reach it."""
    period = _RETURN_SORT.get(sort_by)
    if not period:
        return rows
    return [{**r, sort_by: (r.get("returns") or {}).get(period)} for r in rows]


@router.get("/", summary="MUFAP service info")
async def mufap_root() -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    meta = snapshot.meta or {}
    return {
        "service": "MUFAP Mutual Funds",
        "funds": snapshot.count,
        "categories": len(meta.get("categories", [])),
        "amcs": meta.get("amc_count"),
        "freshness": snapshot.freshness(_stale_after()),
    }


@router.get("/funds", summary="All funds, filtered and paginated")
async def list_funds(
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("fund_name"),
    ascending: bool = Query(True),
    search: Optional[str] = Query(None, description="Match fund name or AMC"),
    category: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    amc: Optional[str] = Query(None),
    trustee: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    min_nav: Optional[float] = Query(None, ge=0),
    max_nav: Optional[float] = Query(None, ge=0),
    min_ytd: Optional[float] = Query(None, description="Minimum year-to-date return %"),
) -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    rows = require_rows(snapshot, "MUFAP fund data")

    filters = []
    if search:
        filters.append(lambda r: contains(r.get("fund_name"), search) or contains(r.get("amc"), search))
    if category:
        filters.append(lambda r: contains(r.get("category"), category))
    if sector:
        filters.append(lambda r: contains(r.get("sector"), sector))
    if amc:
        filters.append(lambda r: contains(r.get("amc"), amc))
    if trustee:
        filters.append(lambda r: contains(r.get("trustee"), trustee))
    if rating:
        filters.append(lambda r: contains(r.get("rating"), rating))
    if min_nav is not None:
        filters.append(lambda r: (r.get("nav") or 0) >= min_nav)
    if max_nav is not None:
        filters.append(lambda r: (r.get("nav") or 0) <= max_nav)
    if min_ytd is not None:
        filters.append(lambda r: ((r.get("returns") or {}).get("ytd") or float("-inf")) >= min_ytd)

    filtered = apply_filters(rows, filters)
    ordered = sort_rows(_flatten_for_sort(filtered, sort_by), sort_by, ascending, SORTABLE)
    page = [_shape(r) for r in paginate(ordered, offset, limit)]

    return envelope(page, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(filtered), offset=offset, limit=limit)


@router.get("/funds/search", summary="Search funds by name or AMC")
async def search_funds(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    rows = require_rows(snapshot, "MUFAP fund data")
    matches = [r for r in rows
               if contains(r.get("fund_name"), q) or contains(r.get("amc"), q)]
    matches.sort(key=lambda r: r.get("fund_name") or "")
    page = [_shape(r) for r in matches[:limit]]
    return envelope(page, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(matches), offset=0, limit=limit, extra={"query": q})


@router.get("/funds/categories", summary="Fund categories with counts")
async def categories() -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    require_rows(snapshot, "MUFAP fund data")
    rows = (snapshot.meta or {}).get("categories", [])
    return envelope(rows, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(rows), offset=0, limit=None)


@router.get("/funds/amcs", summary="Asset management companies with fund counts")
async def amcs() -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    rows = require_rows(snapshot, "MUFAP fund data")
    counts: dict[str, int] = {}
    for row in rows:
        name = row.get("amc")
        if name:
            counts[name] = counts.get(name, 0) + 1
    listing = [{"amc": k, "count": v} for k, v in sorted(counts.items())]
    return envelope(listing, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(listing), offset=0, limit=None)


@router.get("/funds/top", summary="Best performers over a chosen period")
async def top_funds(
    period: str = Query("ytd", description="ytd | mtd | d1 | d30 | d90 | d365 | y2 | y3"),
    limit: int = Query(20, ge=1, le=200),
    category: Optional[str] = Query(None),
) -> dict[str, Any]:
    valid = {key for key, _ in RETURN_PERIODS}
    if period not in valid:
        from ..infra.errors import BadRequest
        raise BadRequest(f"Unknown period '{period}'. Valid: {', '.join(sorted(valid))}.")

    snapshot = await service.get_funds_snapshot()
    rows = require_rows(snapshot, "MUFAP fund data")
    pool = [r for r in rows if (r.get("returns") or {}).get(period) is not None]
    if category:
        pool = [r for r in pool if contains(r.get("category"), category)]
    pool.sort(key=lambda r: r["returns"][period], reverse=True)
    page = [_shape(r) for r in pool[:limit]]
    return envelope(page, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(pool), offset=0, limit=limit,
                    extra={"period": period})


@router.get("/funds/top-nav", summary="Highest NAV funds")
async def top_nav(
    limit: int = Query(20, ge=1, le=200),
    category: Optional[str] = Query(None),
) -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    rows = require_rows(snapshot, "MUFAP fund data")
    pool = [r for r in rows if r.get("nav") is not None]
    if category:
        pool = [r for r in pool if contains(r.get("category"), category)]
    pool.sort(key=lambda r: r["nav"], reverse=True)
    page = [_shape(r) for r in pool[:limit]]
    return envelope(page, snapshot=snapshot, stale_after_s=_stale_after(),
                    total=len(pool), offset=0, limit=limit)


@router.get("/funds/stats", summary="Aggregate NAV and return statistics")
async def stats(category: Optional[str] = Query(None)) -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    rows = require_rows(snapshot, "MUFAP fund data")

    if category is None:
        return {
            **(snapshot.meta or {}).get("stats", {}),
            "category_filter": None,
            "freshness": snapshot.freshness(_stale_after()),
        }

    subset = [r for r in rows if contains(r.get("category"), category)]
    if not subset:
        raise NotFound(f"No funds match category '{category}'.")
    return {
        **service._build_stats(subset),
        "category_filter": category,
        "freshness": snapshot.freshness(_stale_after()),
    }


@router.get("/funds/{fund_name:path}", summary="One fund by exact name")
async def fund_detail(fund_name: str) -> dict[str, Any]:
    snapshot = await service.get_funds_snapshot()
    rows = require_rows(snapshot, "MUFAP fund data")
    target = fund_name.strip().lower()
    matches = [r for r in rows if (r.get("fund_name") or "").lower() == target]
    if not matches:
        raise NotFound(f"No fund named '{fund_name}'.")
    # A VPS fund name can cover several sub-funds; return them all.
    if len(matches) == 1:
        return single(_shape(matches[0]), snapshot=snapshot, stale_after_s=_stale_after())
    return envelope([_shape(r) for r in matches], snapshot=snapshot,
                    stale_after_s=_stale_after(), total=len(matches),
                    offset=0, limit=None)


@router.post("/refresh", dependencies=[Depends(require_internal_token)],
             summary="Force a MUFAP refresh (authenticated)")
async def refresh() -> dict[str, Any]:
    snapshot = await service.refresh_funds(force=True)
    return {"count": snapshot.count, "as_of": snapshot.data_as_of, "error": snapshot.error}
