# Fintraxa — Pakistan Finance Dashboard & API

<p align="center">
  <img src="frontend-service/public/icons/fintraxa.png" alt="Fintraxa" width="80" />
</p>

**API:** [https://api.fintraxa.com](https://api.fintraxa.com)  
**Dashboard:** [https://fintraxa.com](https://fintraxa.com)

Split-service architecture: **Backend API** (FastAPI) + **Frontend Dashboard** (nginx) on separate ports.

| Data Source | API Prefix | Records |
|---|---|---|
| [MUFAP](https://www.mufap.com.pk) — Mutual Funds | `/api/mufap/...` | ~520 funds |
| [PSX](https://dps.psx.com.pk) — Stock Exchange | `/api/psx/...` | ~470 stocks |
| Swagger UI (Auto Docs) | `/docs` | [Open Docs](https://api.fintraxa.com/docs) |

| Service | Port | Description |
|---|---|---|
| Backend API | `8000` | FastAPI — data scraping & JSON API |
| Frontend | `3000` | nginx — static PWA dashboard |

Data **auto-scrapes every 30 minutes** and is served from an in-memory cache for instant responses.

---

## Live API Base URL

```
https://api.fintraxa.com
```

All API examples below use this base URL. Replace with `http://localhost:8000` when running locally.

---

## Frontend Dashboard

The dashboard is a **React 19 + Vite 6** single-page PWA served by nginx on port **3000**:

- **Mutual Funds tab** — Browse, search, filter by category (~520 Pakistani mutual funds with NAV data). Each fund card shows the AMC/bank logo via Clearbit with colored-initial fallback
- **Stocks tab** — View all PSX-listed stocks with price, change, and volume; sub-tabs for All / Gainers / Losers / Most Active. Each stock row shows the company logo
- **Indices tab** — Live PSX indices (KSE-100, KSE-30, KMI-30, etc.)
- **Responsive layout** — Mobile-first with bottom nav; desktop sidebar navigation at 769px+
- **Section branding** — MUFAP logo header for Mutual Funds, PSX logo header for Stocks
- **Mobile installable** — Add to home screen on Android/iOS for native app experience

**Tech:** React 19 · Vite 6 · MUI Icons 6.4 · CSS variables (light/dark ready) · nginx

---

## API Reference

### Health Check

```bash
curl https://api.fintraxa.com/api/health
```

Returns overall service status with cached record counts:

```json
{
  "status": "healthy",
  "mufap": { "ready": true, "cached": 519 },
  "psx": { "ready": true, "cached": 472 }
}
```

---

### MUFAP Mutual Funds (`/api/mufap/...`)

#### All Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/mufap/` | Service info (version, fund count, last scrape) |
| GET | `/api/mufap/health` | Health check with cache status |
| GET | `/api/mufap/funds` | All funds — filter, sort, paginate |
| GET | `/api/mufap/funds/search?q=` | Search by name, category, or trustee |
| GET | `/api/mufap/funds/categories` | All categories with fund counts |
| GET | `/api/mufap/funds/category/{name}` | All funds in a specific category |
| GET | `/api/mufap/funds/top-nav?limit=` | Top N funds by NAV value |
| GET | `/api/mufap/funds/stats` | Aggregate statistics (avg, min, max NAV) |
| POST | `/api/mufap/scrape` | Trigger background scrape |
| POST | `/api/mufap/scrape/sync` | Trigger scrape and wait for result |

#### Query Parameters for `/api/mufap/funds`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by category (partial match) |
| `trustee` | string | — | Filter by trustee (partial match) |
| `min_nav` | float | — | Minimum NAV |
| `max_nav` | float | — | Maximum NAV |
| `sort_by` | string | `fund_name` | Column to sort by |
| `ascending` | bool | `true` | Sort direction |
| `limit` | int | `1000` | Max results (1–5000) |
| `offset` | int | `0` | Skip N results (pagination) |

#### Query Parameters for `/api/mufap/funds/search`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | *(required)* | Search term |
| `field` | string | `fund_name` | Column to search in |

#### Examples

```bash
# Get all funds
curl "https://api.fintraxa.com/api/mufap/funds"

# Filter by category, sorted by NAV descending, limit 20
curl "https://api.fintraxa.com/api/mufap/funds?category=Equity&sort_by=nav&ascending=false&limit=20"

# Filter by NAV range
curl "https://api.fintraxa.com/api/mufap/funds?min_nav=10&max_nav=100"

# Filter by trustee
curl "https://api.fintraxa.com/api/mufap/funds?trustee=MCB"

# Paginate — page 2 with 50 per page
curl "https://api.fintraxa.com/api/mufap/funds?limit=50&offset=50"

# Search funds by name
curl "https://api.fintraxa.com/api/mufap/funds/search?q=UBL"

# Search by category field
curl "https://api.fintraxa.com/api/mufap/funds/search?q=Equity&field=fund_category"

# Get all categories
curl "https://api.fintraxa.com/api/mufap/funds/categories"

# Get funds in a specific category
curl "https://api.fintraxa.com/api/mufap/funds/category/Equity%20Fund"

# Top 10 funds by NAV
curl "https://api.fintraxa.com/api/mufap/funds/top-nav?limit=10"

# Aggregate statistics
curl "https://api.fintraxa.com/api/mufap/funds/stats"

# Trigger a manual scrape
curl -X POST "https://api.fintraxa.com/api/mufap/scrape/sync"
```

---

### PSX Stock Exchange (`/api/psx/...`)

#### All Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/psx/` | Service info (version, stock count, last scrape) |
| GET | `/api/psx/health` | Health check with cache status |
| GET | `/api/psx/stocks` | All stocks — filter, sort, paginate |
| GET | `/api/psx/stocks/search?symbol=` | Search stocks by symbol |
| GET | `/api/psx/stocks/{symbol}` | Single stock detail |
| GET | `/api/psx/stocks/gainers?limit=` | Top N gainers by change % |
| GET | `/api/psx/stocks/losers?limit=` | Top N losers by change % |
| GET | `/api/psx/stocks/active?limit=` | Most active by trading volume |
| GET | `/api/psx/stocks/summary` | Market overview (totals, gainers/losers count) |
| GET | `/api/psx/indices` | PSX indices (KSE-100, KSE-30, etc.) |
| POST | `/api/psx/scrape` | Trigger background scrape |
| POST | `/api/psx/scrape/sync` | Trigger scrape and wait for result |
| POST | `/api/psx/scrape/indices` | Scrape indices only |

#### Query Parameters for `/api/psx/stocks`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `min_price` | float | — | Minimum current price |
| `max_price` | float | — | Maximum current price |
| `min_volume` | int | — | Minimum trading volume |
| `min_change_pct` | float | — | Minimum change % |
| `max_change_pct` | float | — | Maximum change % |
| `sort_by` | string | `volume` | Column to sort by |
| `ascending` | bool | `false` | Sort direction |
| `limit` | int | `1000` | Max results (1–5000) |
| `offset` | int | `0` | Skip N results (pagination) |

#### Query Parameters for Gainers / Losers / Active

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `20` | Number of results (1–100) |

#### Examples

```bash
# Get all stocks (default: sorted by volume desc)
curl "https://api.fintraxa.com/api/psx/stocks"

# Filter by minimum volume, sorted by change %
curl "https://api.fintraxa.com/api/psx/stocks?min_volume=1000000&sort_by=change_pct&ascending=false"

# Filter by price range
curl "https://api.fintraxa.com/api/psx/stocks?min_price=50&max_price=500"

# Filter by positive change only
curl "https://api.fintraxa.com/api/psx/stocks?min_change_pct=0&sort_by=change_pct&ascending=false"

# Paginate — page 3 with 100 per page
curl "https://api.fintraxa.com/api/psx/stocks?limit=100&offset=200"

# Search stocks by symbol
curl "https://api.fintraxa.com/api/psx/stocks/search?symbol=HBL"

# Get a single stock
curl "https://api.fintraxa.com/api/psx/stocks/ENGRO"

# Top 10 gainers
curl "https://api.fintraxa.com/api/psx/stocks/gainers?limit=10"

# Top 10 losers
curl "https://api.fintraxa.com/api/psx/stocks/losers?limit=10"

# Most active stocks by volume
curl "https://api.fintraxa.com/api/psx/stocks/active?limit=15"

# Market summary
curl "https://api.fintraxa.com/api/psx/stocks/summary"

# PSX indices (KSE-100, KSE-30, KMI-30, etc.)
curl "https://api.fintraxa.com/api/psx/indices"

# Trigger a manual scrape
curl -X POST "https://api.fintraxa.com/api/psx/scrape/sync"

# Scrape indices only
curl -X POST "https://api.fintraxa.com/api/psx/scrape/indices"
```

---

## Response Format

All list endpoints return a consistent JSON structure:

```json
{
  "count": 20,
  "total_filtered": 519,
  "total_available": 519,
  "offset": 0,
  "limit": 20,
  "last_scrape": "2026-02-28T14:30:00",
  "data": [
    { "fund_name": "...", "nav": 12.34, ... }
  ]
}
```

---

## Quick Start (Local Development)

### Option 1: Docker (recommended)

```bash
docker compose up -d --build
# Backend API: http://localhost:8000
# Frontend:    http://localhost:3000
# Swagger UI:  http://localhost:8000/docs
```

### Option 2: Run Locally

**Backend API:**
```bash
cd api-service
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn main:app --port 8000
```

**Frontend (requires Node.js):**
```bash
cd frontend-service
npm install
npm run dev
# Dev server: http://localhost:5173
```

For production build:
```bash
npm run build   # outputs to dist/
```

### Configuration

| Variable | Default | Description |
|---|---|---|
| `SCRAPE_INTERVAL_MINUTES` | `30` | Auto-scrape frequency |
| `PORT` | `8000` | Backend API port |

---

## Architecture

```
   ┌─────────────┐      ┌─────────────────────────────┐
   │  MUFAP Site │◄─────│  PK Finance API Service     │ :8000
   └─────────────┘      │                             │
   ┌─────────────┐      │  /api/mufap/*  (FastAPI)    │
   │  PSX Site   │◄─────│  /api/psx/*    (FastAPI)    │
   └─────────────┘      │  In-memory cache + 30m loop │
                         └──────────────▲──────────────┘
                                        │ fetch()
                         ┌──────────────┴──────────────┐
                         │  PK Finance Frontend        │ :3000
                         │  nginx  ─ static PWA        │
                         │  index.html + sw.js          │──────►  Browser
                         └─────────────────────────────┘
```

**API URL:** https://api.fintraxa.com

**Tech Stack:** Python 3.12 · FastAPI · BeautifulSoup4 + lxml · Pandas · ORJSONResponse · GZip · React 19 · Vite 6 · MUI Icons · nginx · Multi-stage Docker

---

## Project Structure

```
Microservices/
├── docker-compose.yml
├── README.md
├── .gitignore
├── api-service/                    # ← Backend API (port 8000)
│   ├── main.py                     # FastAPI app (unified API gateway)
│   ├── mufap_scraper.py            # MUFAP web scraper
│   ├── psx_scraper.py              # PSX web scraper
│   ├── config.py                   # Configuration
│   ├── requirements.txt
│   └── Dockerfile
├── frontend-service/               # ← Frontend Dashboard (port 3000)
│   ├── index.html                  # Entry HTML with favicon/PWA meta
│   ├── vite.config.js              # Vite 6 build config
│   ├── package.json                # React 19, MUI Icons, Vite
│   ├── nginx.conf                  # nginx config (gzip, caching)
│   ├── Dockerfile                  # Multi-stage Node 22 + nginx
│   ├── public/
│   │   ├── manifest.json           # PWA manifest
│   │   ├── sw.js                   # Service worker
│   │   └── icons/                  # Fintraxa, MUFAP, PSX logos + favicons
│   └── src/
│       ├── main.jsx                # React entry point
│       ├── App.jsx                 # Root component (tabs, health polling)
│       ├── App.css                 # All styles (responsive, dark-ready)
│       ├── api.js                  # API client (fintraxa.com endpoints)
│       └── components/
│           ├── Header.jsx          # Top bar with logo + health badges
│           ├── BottomNav.jsx       # Mobile bottom navigation
│           ├── SideNav.jsx         # Desktop sidebar navigation
│           ├── FundsView.jsx       # Mutual funds list + categories
│           ├── StocksView.jsx      # PSX stocks (all/gainers/losers/active)
│           ├── IndicesView.jsx     # Market indices grid
│           ├── BankLogo.jsx        # Company logo (Clearbit + initial fallback)
│           └── Skeleton.jsx        # Loading shimmer components
├── unified-service/                # Legacy combined service (deprecated)
├── Mutual Funds Data Micorservice/ # Original scraper (reference)
└── Psx Data Reader microservice/   # Original scraper (reference)
```
