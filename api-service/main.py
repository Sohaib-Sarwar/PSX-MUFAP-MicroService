"""
PK Finance API Service
============================
FastAPI backend providing:
  - MUFAP Mutual Funds API  → /api/mufap/...
  - PSX Stock Exchange API   → /api/psx/...

Frontend is deployed separately as a static site.
"""

import gc
import asyncio
import logging
from datetime import timedelta
from contextlib import asynccontextmanager
from typing import Optional
from threading import Lock

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, APIRouter
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import pandas as pd

from config import SCRAPE_INTERVAL_MINUTES, now_utc5
from mufap_scraper import scrape_mufap_nav_data
from psx_scraper import scrape_psx_market_watch, scrape_psx_indices

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
#  In-memory caches
# ══════════════════════════════════════════════════════════════════

# MUFAP
_mufap_data: Optional[pd.DataFrame] = None
_mufap_last_scrape: Optional[str] = None
_mufap_scrape_count: int = 0
_mufap_lock = Lock()
_mufap_category_cache: list[dict] = []
_mufap_stats_cache: dict = {}

# PSX
_psx_stock_data: Optional[pd.DataFrame] = None
_psx_index_data: Optional[pd.DataFrame] = None
_psx_last_scrape: Optional[str] = None
_psx_scrape_count: int = 0
_psx_lock = Lock()
_psx_summary_cache: dict = {}

_next_scrape_time: Optional[str] = None


# ══════════════════════════════════════════════════════════════════
#  MUFAP helpers
# ══════════════════════════════════════════════════════════════════

def _mufap_rebuild_caches(df: pd.DataFrame):
    global _mufap_category_cache, _mufap_stats_cache
    cat_counts = df["fund_category"].value_counts()
    _mufap_category_cache = [
        {"category": k, "count": int(v)} for k, v in sorted(cat_counts.items())
    ]
    nav = df["nav"]
    _mufap_stats_cache = {
        "total_funds": len(df),
        "total_categories": int(df["fund_category"].nunique()),
        "nav": {
            "mean": round(float(nav.mean()), 4),
            "median": round(float(nav.median()), 4),
            "min": round(float(nav.min()), 4),
            "max": round(float(nav.max()), 4),
            "std": round(float(nav.std()), 4),
        },
        "offer_price": {
            "mean": round(float(df["offer_price"].mean()), 4) if "offer_price" in df.columns else None,
            "min": round(float(df["offer_price"].min()), 4) if "offer_price" in df.columns else None,
            "max": round(float(df["offer_price"].max()), 4) if "offer_price" in df.columns else None,
        },
        "data_date": df["date_updated"].mode().iloc[0] if not df["date_updated"].mode().empty else None,
        "trustee_count": int(df["trustee"].nunique()) if "trustee" in df.columns else 0,
    }


def _mufap_scrape():
    global _mufap_data, _mufap_last_scrape, _mufap_scrape_count
    if not _mufap_lock.acquire(blocking=False):
        logger.info("MUFAP scrape already running – skipping")
        return {"status": "skipped", "reason": "already_running"}
    try:
        logger.info("Starting MUFAP scrape...")
        df = scrape_mufap_nav_data()
        if df.empty:
            logger.warning("MUFAP scrape returned no data")
            return {"status": "no_data", "count": 0}
        df = _downcast_df(df)
        old = _mufap_data
        _mufap_data = df
        del old  # explicitly drop old DF reference
        _mufap_last_scrape = now_utc5().isoformat()
        _mufap_scrape_count += 1
        _mufap_rebuild_caches(df)
        _release_memory()
        logger.info(f"MUFAP scraped {len(df)} funds")
        return {"status": "success", "count": len(df), "scraped_at": _mufap_last_scrape}
    except Exception as e:
        logger.error(f"MUFAP scrape failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        _mufap_lock.release()


def _get_mufap_data() -> pd.DataFrame:
    if _mufap_data is not None and not _mufap_data.empty:
        return _mufap_data
    raise HTTPException(404, "No MUFAP data yet. Service is still loading.")


# ══════════════════════════════════════════════════════════════════
#  PSX helpers
# ══════════════════════════════════════════════════════════════════

def _psx_rebuild_caches(df: pd.DataFrame):
    global _psx_summary_cache
    total = len(df)
    gainers = int((df["change"] > 0).sum()) if "change" in df.columns else 0
    losers = int((df["change"] < 0).sum()) if "change" in df.columns else 0
    unchanged = total - gainers - losers
    total_volume = int(df["volume"].sum()) if "volume" in df.columns else 0
    _psx_summary_cache = {
        "total_stocks": total,
        "gainers": gainers,
        "losers": losers,
        "unchanged": unchanged,
        "total_volume": total_volume,
        "avg_change_pct": round(float(df["change_pct"].mean()), 2) if "change_pct" in df.columns else None,
        "total_traded_value": round(float((df["current"] * df["volume"]).sum()), 0) if {"current", "volume"} <= set(df.columns) else None,
        "market_date": df["date"].iloc[0] if "date" in df.columns and not df.empty else None,
    }


def _psx_scrape():
    global _psx_stock_data, _psx_index_data, _psx_last_scrape, _psx_scrape_count
    if not _psx_lock.acquire(blocking=False):
        logger.info("PSX scrape already running – skipping")
        return {"status": "skipped", "reason": "already_running"}
    try:
        logger.info("Starting PSX scrape...")
        df_stocks = scrape_psx_market_watch()
        if df_stocks.empty:
            logger.warning("PSX scrape returned no data")
            return {"status": "no_data", "stocks": 0, "indices": 0}
        df_stocks = _downcast_df(df_stocks)
        old_stocks = _psx_stock_data
        _psx_stock_data = df_stocks
        del old_stocks  # explicitly drop old DF reference
        _psx_last_scrape = now_utc5().isoformat()
        _psx_scrape_count += 1
        _psx_rebuild_caches(df_stocks)
        df_indices = scrape_psx_indices()
        if not df_indices.empty:
            df_indices = _downcast_df(df_indices)
        old_idx = _psx_index_data
        _psx_index_data = df_indices
        del old_idx  # explicitly drop old index DF
        _release_memory()
        logger.info(f"PSX scraped {len(df_stocks)} stocks, {len(df_indices)} indices")
        return {"status": "success", "stocks": len(df_stocks), "indices": len(df_indices), "scraped_at": _psx_last_scrape}
    except Exception as e:
        logger.error(f"PSX scrape failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        _psx_lock.release()


def _get_psx_data() -> pd.DataFrame:
    if _psx_stock_data is not None and not _psx_stock_data.empty:
        return _psx_stock_data
    raise HTTPException(404, "No PSX data yet. Service is still loading.")


# ══════════════════════════════════════════════════════════════════
#  Background scrape loop (single loop for both sources)
# ══════════════════════════════════════════════════════════════════

def _downcast_df(df: pd.DataFrame) -> pd.DataFrame:
    """Reduce DataFrame memory by downcasting numeric columns."""
    for col in df.select_dtypes(include=["float64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="float")
    for col in df.select_dtypes(include=["int64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="integer")
    # Convert low-cardinality string columns to category dtype (saves ~80% RAM)
    for col in df.select_dtypes(include=["object"]).columns:
        if df[col].nunique() < len(df) * 0.5:  # < 50% unique → use category
            df[col] = df[col].astype("category")
    return df


def _release_memory():
    """Force Python GC and release freed pages back to the OS (Linux/Docker)."""
    gc.collect()
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)  # return freed heap pages to OS
    except (OSError, AttributeError):
        pass  # not Linux / not glibc – skip


async def _scrape_loop():
    while True:
        global _next_scrape_time
        _next_scrape_time = (now_utc5() + timedelta(minutes=SCRAPE_INTERVAL_MINUTES)).isoformat()
        await asyncio.sleep(SCRAPE_INTERVAL_MINUTES * 60)
        await asyncio.to_thread(_mufap_scrape)
        await asyncio.to_thread(_psx_scrape)
        _release_memory()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("  PK Finance Unified Service Starting...")
    logger.info(f"  Scrape interval: every {SCRAPE_INTERVAL_MINUTES} min")
    logger.info("=" * 60)

    # Initial scrape (both sources, in threads)
    await asyncio.to_thread(_mufap_scrape)
    await asyncio.to_thread(_psx_scrape)
    _release_memory()

    # Start repeating loop
    task = asyncio.create_task(_scrape_loop())
    logger.info(f"Background scrape loop started – {SCRAPE_INTERVAL_MINUTES} min interval")
    yield
    task.cancel()
    logger.info("PK Finance Unified Service shutting down")


# ══════════════════════════════════════════════════════════════════
#  App + Routers
# ══════════════════════════════════════════════════════════════════

app = FastAPI(
    title="PK Finance API Service",
    description=(
        "Backend API for Pakistan financial data.\n\n"
        "- **MUFAP Mutual Funds** → `/api/mufap/...`\n"
        "- **PSX Stock Exchange** → `/api/psx/...`"
    ),
    version="5.0.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Root health ──────────────────────────────────────────────────

@app.get("/api/health")
async def unified_health():
    mufap_ok = _mufap_data is not None and not _mufap_data.empty
    psx_ok = _psx_stock_data is not None and not _psx_stock_data.empty
    return {
        "status": "healthy" if (mufap_ok and psx_ok) else "warming_up",
        "mufap": {"ready": mufap_ok, "cached": len(_mufap_data) if mufap_ok else 0, "last_scrape": _mufap_last_scrape},
        "psx": {"ready": psx_ok, "cached": len(_psx_stock_data) if psx_ok else 0, "last_scrape": _psx_last_scrape},
        "next_scrape": _next_scrape_time,
    }


@app.get("/api/debug/memory")
async def debug_memory():
    """Memory usage info for debugging."""
    import sys
    mufap_bytes = 0
    psx_bytes = 0
    idx_bytes = 0
    if _mufap_data is not None:
        mufap_bytes = int(_mufap_data.memory_usage(deep=True).sum())
    if _psx_stock_data is not None:
        psx_bytes = int(_psx_stock_data.memory_usage(deep=True).sum())
    if _psx_index_data is not None:
        idx_bytes = int(_psx_index_data.memory_usage(deep=True).sum())
    import psutil, os as _os
    proc = psutil.Process(_os.getpid())
    mem = proc.memory_info()
    return {
        "process_rss_mb": round(mem.rss / (1024 * 1024), 2),
        "process_vms_mb": round(mem.vms / (1024 * 1024), 2),
        "dataframes_mb": round((mufap_bytes + psx_bytes + idx_bytes) / (1024 * 1024), 2),
        "mufap_mb": round(mufap_bytes / (1024 * 1024), 2),
        "psx_mb": round(psx_bytes / (1024 * 1024), 2),
        "indices_mb": round(idx_bytes / (1024 * 1024), 2),
        "gc_tracked_objects": len(gc.get_objects()),
        "scrape_count": _mufap_scrape_count + _psx_scrape_count,
    }


# ══════════════════════════════════════════════════════════════════
#  MUFAP Router  →  /api/mufap/...
# ══════════════════════════════════════════════════════════════════

mufap = APIRouter(prefix="/api/mufap", tags=["MUFAP Mutual Funds"])


@mufap.get("/")
async def mufap_root():
    return {
        "service": "MUFAP Mutual Funds",
        "status": "running",
        "last_scrape": _mufap_last_scrape,
        "scrape_count": _mufap_scrape_count,
        "cached_funds": len(_mufap_data) if _mufap_data is not None else 0,
        "auto_refresh_minutes": SCRAPE_INTERVAL_MINUTES,
    }


@mufap.get("/health")
async def mufap_health():
    has_data = _mufap_data is not None and not _mufap_data.empty
    return {
        "status": "healthy" if has_data else "warming_up",
        "ready": has_data,
        "last_scrape": _mufap_last_scrape,
        "scrape_count": _mufap_scrape_count,
        "cached_records": len(_mufap_data) if has_data else 0,
        "next_scrape": _next_scrape_time,
    }


@mufap.post("/scrape")
async def mufap_trigger_scrape(background_tasks: BackgroundTasks):
    background_tasks.add_task(_mufap_scrape)
    return {"status": "scrape_started", "message": "Scraping MUFAP in background."}


@mufap.post("/scrape/sync")
async def mufap_scrape_sync():
    return _mufap_scrape()


@mufap.get("/funds")
async def get_funds(
    category: Optional[str] = Query(None),
    trustee: Optional[str] = Query(None),
    min_nav: Optional[float] = Query(None, ge=0),
    max_nav: Optional[float] = Query(None, ge=0),
    sort_by: str = Query("fund_name"),
    ascending: bool = Query(True),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    df = _get_mufap_data()
    if category:
        df = df[df["fund_category"].str.contains(category, case=False, na=False)]
    if trustee:
        df = df[df["trustee"].str.contains(trustee, case=False, na=False)]
    if min_nav is not None:
        df = df[df["nav"] >= min_nav]
    if max_nav is not None:
        df = df[df["nav"] <= max_nav]
    total_filtered = len(df)
    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=ascending, na_position="last")
    df = df.iloc[offset: offset + limit]
    return {
        "count": len(df), "total_filtered": total_filtered,
        "total_available": len(_mufap_data), "offset": offset, "limit": limit,
        "last_scrape": _mufap_last_scrape, "data": df.to_dict(orient="records"),
    }


@mufap.get("/funds/search")
async def search_funds(
    q: str = Query(..., min_length=1),
    field: str = Query("fund_name"),
):
    df = _get_mufap_data()
    if field not in df.columns:
        raise HTTPException(400, f"Invalid field '{field}'")
    df = df[df[field].str.contains(q, case=False, na=False)]
    return {"query": q, "field": field, "count": len(df), "data": df.to_dict(orient="records")}


@mufap.get("/funds/categories")
async def list_categories():
    _get_mufap_data()
    return {"total_categories": len(_mufap_category_cache), "categories": _mufap_category_cache}


@mufap.get("/funds/category/{category}")
async def get_funds_by_category(category: str):
    df = _get_mufap_data()
    df = df[df["fund_category"].str.contains(category, case=False, na=False)]
    if df.empty:
        raise HTTPException(404, f"No funds for category '{category}'")
    return {"category": category, "count": len(df), "data": df.to_dict(orient="records")}


@mufap.get("/funds/top-nav")
async def top_nav_funds(limit: int = Query(20, ge=1, le=100), category: Optional[str] = Query(None)):
    df = _get_mufap_data()
    if category:
        df = df[df["fund_category"].str.contains(category, case=False, na=False)]
    df = df.nlargest(limit, "nav")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@mufap.get("/funds/stats")
async def fund_stats(category: Optional[str] = Query(None)):
    if category is None:
        _get_mufap_data()
        return {**_mufap_stats_cache, "last_scrape": _mufap_last_scrape, "category_filter": None}
    df = _get_mufap_data()
    df = df[df["fund_category"].str.contains(category, case=False, na=False)]
    if df.empty:
        raise HTTPException(404, "No data matches the filter")
    nav = df["nav"]
    return {
        "total_funds": len(df), "total_categories": int(df["fund_category"].nunique()),
        "nav": {"mean": round(float(nav.mean()), 4), "median": round(float(nav.median()), 4),
                "min": round(float(nav.min()), 4), "max": round(float(nav.max()), 4),
                "std": round(float(nav.std()), 4)},
        "last_scrape": _mufap_last_scrape, "category_filter": category,
    }


app.include_router(mufap)


# ══════════════════════════════════════════════════════════════════
#  PSX Router  →  /api/psx/...
# ══════════════════════════════════════════════════════════════════

psx = APIRouter(prefix="/api/psx", tags=["PSX Stock Exchange"])


@psx.get("/")
async def psx_root():
    return {
        "service": "PSX Stock Exchange",
        "status": "running",
        "last_scrape": _psx_last_scrape,
        "scrape_count": _psx_scrape_count,
        "cached_stocks": len(_psx_stock_data) if _psx_stock_data is not None else 0,
        "auto_refresh_minutes": SCRAPE_INTERVAL_MINUTES,
    }


@psx.get("/health")
async def psx_health():
    has_data = _psx_stock_data is not None and not _psx_stock_data.empty
    return {
        "status": "healthy" if has_data else "warming_up",
        "ready": has_data,
        "last_scrape": _psx_last_scrape,
        "scrape_count": _psx_scrape_count,
        "cached_stocks": len(_psx_stock_data) if has_data else 0,
        "cached_indices": len(_psx_index_data) if _psx_index_data is not None else 0,
        "next_scrape": _next_scrape_time,
    }


@psx.post("/scrape")
async def psx_trigger_scrape(background_tasks: BackgroundTasks):
    background_tasks.add_task(_psx_scrape)
    return {"status": "scrape_started", "message": "Scraping PSX in background."}


@psx.post("/scrape/sync")
async def psx_scrape_sync():
    return _psx_scrape()


@psx.post("/scrape/indices")
async def psx_scrape_indices():
    global _psx_index_data
    df = scrape_psx_indices()
    _psx_index_data = df
    records = df.to_dict(orient="records") if not df.empty else []
    return {"count": len(records), "data": records}


@psx.get("/stocks")
async def get_all_stocks(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("volume"),
    ascending: bool = Query(False),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    min_volume: Optional[int] = Query(None, ge=0),
    min_change_pct: Optional[float] = Query(None),
    max_change_pct: Optional[float] = Query(None),
):
    df = _get_psx_data()
    if min_price is not None and "current" in df.columns:
        df = df[df["current"] >= min_price]
    if max_price is not None and "current" in df.columns:
        df = df[df["current"] <= max_price]
    if min_volume is not None and "volume" in df.columns:
        df = df[df["volume"] >= min_volume]
    if min_change_pct is not None and "change_pct" in df.columns:
        df = df[df["change_pct"] >= min_change_pct]
    if max_change_pct is not None and "change_pct" in df.columns:
        df = df[df["change_pct"] <= max_change_pct]
    total_filtered = len(df)
    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=ascending, na_position="last")
    df = df.iloc[offset: offset + limit]
    return {
        "count": len(df), "total_filtered": total_filtered,
        "total": len(_psx_stock_data), "offset": offset, "limit": limit,
        "last_scrape": _psx_last_scrape, "data": df.to_dict(orient="records"),
    }


@psx.get("/stocks/search")
async def search_stocks(symbol: str = Query(..., min_length=1)):
    df = _get_psx_data()
    df = df[df["symbol"].str.contains(symbol.upper(), case=False, na=False)]
    return {"count": len(df), "data": df.to_dict(orient="records")}


@psx.get("/stocks/gainers")
async def top_gainers(limit: int = Query(20, ge=1, le=100)):
    df = _get_psx_data()
    df = df[df["change"] > 0].nlargest(limit, "change_pct")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@psx.get("/stocks/losers")
async def top_losers(limit: int = Query(20, ge=1, le=100)):
    df = _get_psx_data()
    df = df[df["change"] < 0].nsmallest(limit, "change_pct")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@psx.get("/stocks/active")
async def most_active(limit: int = Query(20, ge=1, le=100)):
    df = _get_psx_data()
    df = df.nlargest(limit, "volume")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@psx.get("/stocks/summary")
async def market_summary():
    _get_psx_data()
    return {
        **_psx_summary_cache,
        "last_scrape": _psx_last_scrape,
        "scrape_count": _psx_scrape_count,
        "auto_refresh_minutes": SCRAPE_INTERVAL_MINUTES,
    }


@psx.get("/stocks/{symbol}")
async def stock_detail(symbol: str):
    df = _get_psx_data()
    match = df[df["symbol"].str.upper() == symbol.upper()]
    if match.empty:
        raise HTTPException(404, f"Stock '{symbol}' not found")
    return {"symbol": symbol.upper(), "data": match.iloc[0].to_dict()}


@psx.get("/indices")
async def get_all_indices():
    if _psx_index_data is not None and not _psx_index_data.empty:
        return {"count": len(_psx_index_data), "data": _psx_index_data.to_dict(orient="records")}
    raise HTTPException(404, "No index data. Scrape will run automatically.")


app.include_router(psx)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
