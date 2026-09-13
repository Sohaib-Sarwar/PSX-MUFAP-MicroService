"""
PSX Data Reader Microservice - FastAPI Application
=====================================================
REST API for scraping, storing, and retrieving Pakistan Stock Exchange data.
Auto-scrapes every N minutes via lightweight asyncio background task.

Endpoints:
    GET  /                      - Health check & service info
    GET  /health                - Detailed health/readiness check
    POST /scrape                - Trigger market watch scrape (background)
    POST /scrape/sync           - Trigger sync scrape
    POST /scrape/indices        - Scrape index data
    GET  /stocks                - Get stock data (filter, sort, paginate)
    GET  /stocks/search         - Search stocks by symbol
    GET  /stocks/{symbol}       - Get single stock detail
    GET  /stocks/gainers        - Top gainers
    GET  /stocks/losers         - Top losers
    GET  /stocks/active         - Most active by volume
    GET  /stocks/summary        - Market overview stats
    GET  /indices               - Get index data
    GET  /export/excel          - Download Excel report
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional
from threading import Lock

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import pandas as pd

from config import EXCEL_OUTPUT_DIR, SCRAPE_INTERVAL_MINUTES, now_utc5
from scraper import scrape_psx_market_watch, scrape_psx_indices, scrape_psx_performers

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── In-memory cache ──────────────────────────────────────────────
_stock_data: Optional[pd.DataFrame] = None
_index_data: Optional[pd.DataFrame] = None
_last_scrape_time: Optional[str] = None
_scrape_count: int = 0
_scrape_lock = Lock()
_next_scrape_time: Optional[str] = None

# Pre-computed caches (rebuilt each scrape)
_summary_cache: dict = {}


# ── Lightweight background scheduler (replaces APScheduler) ─────
async def _scrape_loop():
    """Repeat scrape every SCRAPE_INTERVAL_MINUTES using pure asyncio."""
    while True:
        global _next_scrape_time
        _next_scrape_time = (now_utc5() + timedelta(minutes=SCRAPE_INTERVAL_MINUTES)).isoformat()
        await asyncio.sleep(SCRAPE_INTERVAL_MINUTES * 60)
        await asyncio.to_thread(_run_scrape)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    logger.info("=" * 60)
    logger.info("  PSX Data Reader Microservice Starting...")
    logger.info(f"  Auto-scrape interval: every {SCRAPE_INTERVAL_MINUTES} minutes")
    logger.info("=" * 60)
    os.makedirs(EXCEL_OUTPUT_DIR, exist_ok=True)

    # Initial scrape
    await asyncio.to_thread(_run_scrape)

    # Start repeating loop
    task = asyncio.create_task(_scrape_loop())
    logger.info(f"Background scrape loop started – interval {SCRAPE_INTERVAL_MINUTES} min")

    yield

    task.cancel()
    logger.info("Shutting down PSX Data Reader Microservice")


app = FastAPI(
    title="PSX Data Reader Microservice",
    description=(
        "Microservice for scraping and serving Pakistan Stock Exchange market data.\n\n"
        "**Data Source:** https://dps.psx.com.pk/\n\n"
        "Data auto-refreshes every 30 minutes."
    ),
    version="3.0.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

# Compress responses > 500 bytes
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────
#  Helper
# ──────────────────────────────────────────────────────────────────

def _rebuild_caches(df: pd.DataFrame):
    """Pre-compute summary so the endpoint is instant."""
    global _summary_cache

    total = len(df)
    gainers = int((df["change"] > 0).sum()) if "change" in df.columns else 0
    losers = int((df["change"] < 0).sum()) if "change" in df.columns else 0
    unchanged = total - gainers - losers
    total_volume = int(df["volume"].sum()) if "volume" in df.columns else 0

    _summary_cache = {
        "total_stocks": total,
        "gainers": gainers,
        "losers": losers,
        "unchanged": unchanged,
        "total_volume": total_volume,
        "avg_change_pct": round(float(df["change_pct"].mean()), 2) if "change_pct" in df.columns else None,
        "total_traded_value": round(float((df["current"] * df["volume"]).sum()), 0) if {"current", "volume"} <= set(df.columns) else None,
        "market_date": df["date"].iloc[0] if "date" in df.columns and not df.empty else None,
    }


def _run_scrape():
    """Execute the full scrape pipeline (thread-safe)."""
    global _stock_data, _index_data, _last_scrape_time, _scrape_count

    if not _scrape_lock.acquire(blocking=False):
        logger.info("Scrape already in progress – skipping")
        return {"status": "skipped", "reason": "already_running"}

    try:
        logger.info("Starting PSX scrape pipeline...")

        # Scrape market watch
        df_stocks = scrape_psx_market_watch()

        if df_stocks.empty:
            logger.warning("Market watch scrape returned no data")
            return {"status": "no_data", "stocks": 0, "indices": 0}

        _stock_data = df_stocks
        _last_scrape_time = now_utc5().isoformat()
        _scrape_count += 1

        # Rebuild derived caches
        _rebuild_caches(df_stocks)

        # Save to Excel (lazy import)
        from excel_export import save_stocks_to_excel
        excel_path = save_stocks_to_excel(df_stocks)

        # Scrape indices separately
        df_indices = scrape_psx_indices()
        _index_data = df_indices

        return {
            "status": "success",
            "stocks": len(df_stocks),
            "indices": len(df_indices),
            "excel_path": excel_path,
            "scraped_at": _last_scrape_time,
        }
    except Exception as e:
        logger.error(f"Scrape failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        _scrape_lock.release()


def _get_stock_data() -> pd.DataFrame:
    """Return cached stock data or raise 404."""
    if _stock_data is not None and not _stock_data.empty:
        return _stock_data
    raise HTTPException(404, "No data available yet. Service is still loading initial data.")


# ──────────────────────────────────────────────────────────────────
#  Endpoints
# ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "PSX Data Reader Microservice",
        "version": "3.0.0",
        "status": "running",
        "last_scrape": _last_scrape_time,
        "scrape_count": _scrape_count,
        "cached_stocks": len(_stock_data) if _stock_data is not None else 0,
        "auto_refresh_minutes": SCRAPE_INTERVAL_MINUTES,
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    has_data = _stock_data is not None and not _stock_data.empty
    return {
        "status": "healthy" if has_data else "warming_up",
        "ready": has_data,
        "last_scrape": _last_scrape_time,
        "scrape_count": _scrape_count,
        "cached_stocks": len(_stock_data) if has_data else 0,
        "cached_indices": len(_index_data) if _index_data is not None else 0,
        "next_scrape": _next_scrape_time,
    }


@app.post("/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_scrape)
    return {"status": "scrape_started", "message": "Scraping PSX data in background. Check /stocks for results."}


@app.post("/scrape/sync")
async def trigger_scrape_sync():
    return _run_scrape()


@app.post("/scrape/indices")
async def scrape_indices_endpoint():
    global _index_data
    df = scrape_psx_indices()
    _index_data = df
    records = df.to_dict(orient="records") if not df.empty else []
    return {"count": len(records), "data": records}


@app.get("/stocks")
async def get_all_stocks(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("volume", description="Sort field"),
    ascending: bool = Query(False),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    min_volume: Optional[int] = Query(None, ge=0),
    min_change_pct: Optional[float] = Query(None),
    max_change_pct: Optional[float] = Query(None),
):
    """Primary endpoint – filter, sort, paginate stock data."""
    df = _get_stock_data()

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
    records = df.to_dict(orient="records")

    return {
        "count": len(records),
        "total_filtered": total_filtered,
        "total": len(_stock_data),
        "offset": offset,
        "limit": limit,
        "last_scrape": _last_scrape_time,
        "data": records,
    }


@app.get("/stocks/search")
async def search_stocks(
    symbol: str = Query(..., min_length=1),
):
    df = _get_stock_data()
    df = df[df["symbol"].str.contains(symbol.upper(), case=False, na=False)]
    return {"count": len(df), "data": df.to_dict(orient="records")}


@app.get("/stocks/gainers")
async def top_gainers(limit: int = Query(20, ge=1, le=100)):
    df = _get_stock_data()
    df = df[df["change"] > 0].nlargest(limit, "change_pct")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@app.get("/stocks/losers")
async def top_losers(limit: int = Query(20, ge=1, le=100)):
    df = _get_stock_data()
    df = df[df["change"] < 0].nsmallest(limit, "change_pct")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@app.get("/stocks/active")
async def most_active(limit: int = Query(20, ge=1, le=100)):
    df = _get_stock_data()
    df = df.nlargest(limit, "volume")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@app.get("/stocks/summary")
async def market_summary():
    """Instant – served from pre-computed cache."""
    _get_stock_data()  # ensure loaded
    return {
        **_summary_cache,
        "last_scrape": _last_scrape_time,
        "scrape_count": _scrape_count,
        "auto_refresh_minutes": SCRAPE_INTERVAL_MINUTES,
    }


@app.get("/stocks/{symbol}")
async def stock_detail(symbol: str):
    df = _get_stock_data()
    match = df[df["symbol"].str.upper() == symbol.upper()]
    if match.empty:
        raise HTTPException(404, f"Stock '{symbol}' not found")
    return {"symbol": symbol.upper(), "data": match.iloc[0].to_dict()}



@app.get("/indices")
async def get_all_indices():
    if _index_data is not None and not _index_data.empty:
        return {"count": len(_index_data), "data": _index_data.to_dict(orient="records")}
    raise HTTPException(404, "No index data. Run POST /scrape first.")


@app.get("/export/excel")
async def export_excel():
    if not os.path.exists(EXCEL_OUTPUT_DIR):
        raise HTTPException(404, "No Excel files available")

    files = sorted(
        [f for f in os.listdir(EXCEL_OUTPUT_DIR) if f.endswith(".xlsx")],
        reverse=True,
    )

    if not files:
        if _stock_data is not None and not _stock_data.empty:
            from excel_export import save_stocks_to_excel
            filepath = save_stocks_to_excel(_stock_data)
            return FileResponse(
                filepath,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=os.path.basename(filepath),
            )
        raise HTTPException(404, "No Excel files. Run POST /scrape first.")

    filepath = os.path.join(EXCEL_OUTPUT_DIR, files[0])
    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=files[0],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
