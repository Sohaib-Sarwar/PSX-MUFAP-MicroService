"""
MUFAP Mutual Funds Data Microservice - FastAPI Application
============================================================
REST API for scraping, storing, and retrieving Pakistan mutual fund NAV data.
Auto-scrapes every N minutes via a lightweight asyncio background task.

Endpoints:
    GET  /                  - Health check & service info
    POST /scrape            - Trigger a new scrape (background)
    POST /scrape/sync       - Trigger a new scrape (blocking)
    GET  /funds             - Get latest scraped data (filter, sort, paginate)
    GET  /funds/search      - Search funds by name or category
    GET  /funds/categories  - Get fund categories with counts
    GET  /funds/category/{category} - Get all funds in a category
    GET  /funds/top-nav     - Top N funds by NAV
    GET  /funds/stats       - Aggregate statistics
    GET  /export/excel      - Download Excel file
    GET  /health            - Detailed health/readiness check
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
from scraper import scrape_mufap_nav_data

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── In-memory cache ──────────────────────────────────────────────
_latest_data: Optional[pd.DataFrame] = None
_last_scrape_time: Optional[str] = None
_scrape_count: int = 0
_scrape_lock = Lock()
_next_scrape_time: Optional[str] = None

# Pre-computed caches (rebuilt each scrape)
_category_cache: list[dict] = []
_stats_cache: dict = {}


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
    """Application startup and shutdown events."""
    logger.info("=" * 60)
    logger.info("  MUFAP Mutual Funds Data Microservice Starting...")
    logger.info(f"  Auto-scrape interval: every {SCRAPE_INTERVAL_MINUTES} minutes")
    logger.info("=" * 60)
    os.makedirs(EXCEL_OUTPUT_DIR, exist_ok=True)

    # Run initial scrape on startup (in thread to not block uvicorn)
    await asyncio.to_thread(_run_scrape)

    # Start the repeating background loop
    task = asyncio.create_task(_scrape_loop())
    logger.info(f"Background scrape loop started – interval {SCRAPE_INTERVAL_MINUTES} min")

    yield

    task.cancel()
    logger.info("Shutting down MUFAP Mutual Funds Data Microservice")


app = FastAPI(
    title="MUFAP Mutual Funds Data Microservice",
    description=(
        "Microservice for scraping and serving Pakistan mutual fund NAV data "
        "from MUFAP.\n\n"
        "**Data Source:** https://www.mufap.com.pk/Industry/IndustryStatDaily?tab=3\n\n"
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
    """Pre-compute category and stats caches so endpoints are instant."""
    global _category_cache, _stats_cache

    # Categories
    cat_counts = df["fund_category"].value_counts()
    _category_cache = [
        {"category": k, "count": int(v)} for k, v in sorted(cat_counts.items())
    ]

    # Stats
    nav = df["nav"]
    _stats_cache = {
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
        "trustees": sorted(df["trustee"].dropna().unique().tolist()) if "trustee" in df.columns else [],
    }


def _run_scrape():
    """Execute the scrape process and save results (thread-safe)."""
    global _latest_data, _last_scrape_time, _scrape_count

    if not _scrape_lock.acquire(blocking=False):
        logger.info("Scrape already in progress – skipping")
        return {"status": "skipped", "reason": "already_running"}

    try:
        logger.info("Starting scrape job...")
        df = scrape_mufap_nav_data()

        if df.empty:
            logger.warning("Scrape returned no data")
            return {"status": "no_data", "count": 0}

        _latest_data = df
        _last_scrape_time = now_utc5().isoformat()
        _scrape_count += 1

        # Rebuild derived caches
        _rebuild_caches(df)

        # Save to Excel (lazy import – openpyxl only loaded here)
        from excel_export import save_to_excel
        excel_path = save_to_excel(df)
        logger.info(f"Data saved to Excel: {excel_path}")

        return {
            "status": "success",
            "count": len(df),
            "excel_path": excel_path,
            "scraped_at": _last_scrape_time,
        }
    except Exception as e:
        logger.error(f"Scrape failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        _scrape_lock.release()


def _get_data() -> pd.DataFrame:
    """Return cached data or raise 404."""
    if _latest_data is not None and not _latest_data.empty:
        return _latest_data
    raise HTTPException(404, "No data available yet. Service is still loading initial data.")


# ──────────────────────────────────────────────────────────────────
#  Endpoints
# ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "MUFAP Mutual Funds Data Microservice",
        "version": "3.0.0",
        "status": "running",
        "last_scrape": _last_scrape_time,
        "scrape_count": _scrape_count,
        "cached_funds": len(_latest_data) if _latest_data is not None else 0,
        "auto_refresh_minutes": SCRAPE_INTERVAL_MINUTES,
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    has_data = _latest_data is not None and not _latest_data.empty
    return {
        "status": "healthy" if has_data else "warming_up",
        "ready": has_data,
        "last_scrape": _last_scrape_time,
        "scrape_count": _scrape_count,
        "cached_records": len(_latest_data) if has_data else 0,
        "next_scrape": _next_scrape_time,
    }


@app.post("/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_scrape)
    return {"status": "scrape_started", "message": "Scraping in background. Check /funds for results."}


@app.post("/scrape/sync")
async def trigger_scrape_sync():
    return _run_scrape()


@app.get("/funds")
async def get_funds(
    category: Optional[str] = Query(None, description="Filter by fund category (partial match)"),
    trustee: Optional[str] = Query(None, description="Filter by trustee"),
    min_nav: Optional[float] = Query(None, ge=0),
    max_nav: Optional[float] = Query(None, ge=0),
    sort_by: str = Query("fund_name", description="Sort field"),
    ascending: bool = Query(True),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """Primary endpoint – filter, sort, paginate mutual fund NAV data."""
    df = _get_data()

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
    records = df.to_dict(orient="records")

    return {
        "count": len(records),
        "total_filtered": total_filtered,
        "total_available": len(_latest_data),
        "offset": offset,
        "limit": limit,
        "last_scrape": _last_scrape_time,
        "data": records,
    }


@app.get("/funds/search")
async def search_funds(
    q: str = Query(..., min_length=1),
    field: str = Query("fund_name", description="fund_name | fund_category | trustee"),
):
    df = _get_data()
    if field not in df.columns:
        raise HTTPException(400, f"Invalid field '{field}'")
    df = df[df[field].str.contains(q, case=False, na=False)]
    return {"query": q, "field": field, "count": len(df), "data": df.to_dict(orient="records")}


@app.get("/funds/categories")
async def list_categories():
    """Instant – served from pre-computed cache."""
    _get_data()  # ensure data loaded
    return {"total_categories": len(_category_cache), "categories": _category_cache}


@app.get("/funds/category/{category}")
async def get_funds_by_category(category: str):
    df = _get_data()
    df = df[df["fund_category"].str.contains(category, case=False, na=False)]
    if df.empty:
        raise HTTPException(404, f"No funds found for category '{category}'")
    return {"category": category, "count": len(df), "data": df.to_dict(orient="records")}


@app.get("/funds/top-nav")
async def top_nav_funds(
    limit: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None),
):
    df = _get_data()
    if category:
        df = df[df["fund_category"].str.contains(category, case=False, na=False)]
    df = df.nlargest(limit, "nav")
    return {"count": len(df), "data": df.to_dict(orient="records")}


@app.get("/funds/stats")
async def fund_stats(category: Optional[str] = Query(None)):
    """Instant when no filter – served from pre-computed cache."""
    if category is None:
        _get_data()  # ensure loaded
        return {**_stats_cache, "last_scrape": _last_scrape_time, "category_filter": None}

    df = _get_data()
    df = df[df["fund_category"].str.contains(category, case=False, na=False)]
    if df.empty:
        raise HTTPException(404, "No data matches the filter")

    nav = df["nav"]
    return {
        "total_funds": len(df),
        "total_categories": int(df["fund_category"].nunique()),
        "nav": {
            "mean": round(float(nav.mean()), 4),
            "median": round(float(nav.median()), 4),
            "min": round(float(nav.min()), 4),
            "max": round(float(nav.max()), 4),
            "std": round(float(nav.std()), 4),
        },
        "last_scrape": _last_scrape_time,
        "category_filter": category,
    }


@app.get("/export/excel")
async def export_excel():
    if not os.path.exists(EXCEL_OUTPUT_DIR):
        raise HTTPException(404, "No Excel files available")

    files = sorted(
        [f for f in os.listdir(EXCEL_OUTPUT_DIR) if f.endswith(".xlsx")],
        reverse=True,
    )

    if not files:
        if _latest_data is not None and not _latest_data.empty:
            from excel_export import save_to_excel
            filepath = save_to_excel(_latest_data)
            return FileResponse(
                filepath,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=os.path.basename(filepath),
            )
        raise HTTPException(404, "No Excel files available.")

    filepath = os.path.join(EXCEL_OUTPUT_DIR, files[0])
    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=files[0],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
