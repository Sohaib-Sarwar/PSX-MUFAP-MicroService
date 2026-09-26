# PK Finance — Unified Service

Pakistan market data as one API: **PSX equities** and **MUFAP mutual funds**, plus a React dashboard.

| | |
|---|---|
| **Domains** | `/api/psx/*` · `/api/mufap/*` — 31 endpoint shapes, 1,463 files |
| **Coverage** | 747 listed PSX instruments · 18 indices · 552 mutual funds |
| **Deployments** | GitHub Pages + Actions (no server) · Docker · Vercel |
| **Dashboard** | GitHub Pages, or served at `/` by the container |
| **Docs** | `/docs` (Swagger) · `/redoc` · [API reference page](#deploy-to-github-pages) on the site |

Sibling branches: [`pk-micro-service`](../../tree/pk-micro-service) (PSX only) · [`mufap-service`](../../tree/mufap-service) (MUFAP only).

---

## Contents

- [Overview](#overview) · [Architecture](#architecture) · [Project structure](#project-structure)
- [Quick start](#quick-start) · [Configuration](#configuration) · [Docker](#docker)
- [**Architecture**](docs/ARCHITECTURE.md) · [**Scheduling & reliability**](#scheduling-and-reliability) · [**Public API** — live, no key](#public-api--live-now-no-key-required) · [Self-hosted API reference](#api-reference--self-hosted-service) · [Freshness contract](#freshness-contract)
- [Live-data strategy](#live-data-strategy) · [Caching](#caching) · [Error handling](#error-handling)
- [Testing](#testing) · [Health checks](#health-checks) · [Monitoring](#monitoring)
- [Deploy to GitHub Pages](#deploy-to-github-pages) · [Deploy to Vercel](#deploy-to-vercel) · [Deploy with Docker](#deploy-with-docker)
- [Troubleshooting](#troubleshooting) · [License](#license)

---

## Overview

Two upstream sources, neither of which offers a documented API:

| Source | What we take | How |
|---|---|---|
| `dps.psx.com.pk/symbols` | 1,020 listed instruments: name, sector, ETF/debt flags | JSON |
| `dps.psx.com.pk/market-watch` | prices for the ~500 that traded today | HTML table |
| `dps.psx.com.pk/indices` | 18 indices with high/low/change | HTML table |
| `dps.psx.com.pk/timeseries/{int,eod}/{sym}` | intraday and end-of-day series | JSON |
| `mufap.com.pk/…?tab=1` | NAV, rating, benchmark, 12 return periods | HTML table |
| `mufap.com.pk/…?tab=3` | AMC, inception, offer/repurchase, sales loads, trustee | HTML table |

Neither MUFAP tab is complete alone, so both are fetched and joined on
`(sector, fund name, normalised category)` — a 1:1 match across all 546 funds.
The join cannot use fund name alone: 28 VPS pension funds share a name across
two or three sub-allocations, and only Category distinguishes them.

Likewise `market-watch` only carries instruments that traded, so it is merged
with `/symbols` to give the full universe. The 575 that did not trade come back
with `traded: false` and null prices rather than silently missing.

### Features

- Full PSX universe with company names, sectors and ETF/debt classification
- Complete MUFAP records: prices, sales loads, trustee, rating **and** returns
- Explicit freshness on every response — `fresh` / `stale` / `degraded` / `unavailable`
- Market status derived from the live index feed, not a hardcoded calendar
- Strict header-mapped parsers that fail loudly rather than guessing
- Publication gate that refuses a collapsed batch instead of overwriting good data
- Adaptive refresh: fast while PSX trades, idle when it is closed
- Runs long-lived (Docker) or serverless (Vercel + Upstash Redis) from one codebase

---

## Architecture

```
request path — never blocks on an upstream
  client → API layer → snapshot store → response + freshness envelope

refresh path — background only
  scheduler → fetch → parse → validate → publish → snapshot store
  (Docker)                                          ↑
  Vercel Cron → POST /internal/refresh ─────────────┤
  GitHub Actions cron → scripts/scrape.py ──────────┘
    → static API built from the snapshot, served by GitHub Pages

app/
├── infra/     config · http · browser_http · store · parsing · validation
│              responses · errors · security · logging · metrics
├── psx/       parsers · service · router
├── mufap/     parsers · service · router
├── system.py  /health · /ready · /metrics · /internal/refresh
└── main.py    application factory
```

A request handler never touches the network. It reads the current snapshot,
which is always either good data or explicitly marked unavailable.

**Snapshot store** has two backends, chosen by `SNAPSHOT_STORE`:

- `memory` — a long-running process. Holds the current snapshot plus the last
  known good one. That is the whole memory bound; nothing accumulates.
- `redis` — serverless, where process memory does not survive between
  invocations. Backed by Upstash's REST API, so no connection pool is needed.
- `file` — batch jobs. One JSON file per dataset, written atomically. A
  scheduled workflow run reads the snapshot its predecessor committed and writes
  the one its successor will read, so the last-known-good contract survives
  across processes with no database at all.

### Project structure

```
├── app/                    application package (see above)
├── api/index.py            Vercel serverless entrypoint + cron route
├── scripts/
│   ├── scrape.py           one domain's refresh as a batch job
│   └── build_static_api.py snapshots → the static API tree (stdlib only)
├── .github/
│   ├── workflows/          scrape-psx · scrape-mufap · pages · ci
│   └── scripts/            publish-data.sh — writes the service-data branch
├── frontend/               React 19 + Vite 6 dashboard (source)
│   ├── src/lib/            data client, formatting, hooks
│   ├── src/components/     table, freshness strip, shared UI
│   └── src/pages/          overview · stocks · indices · funds · API reference
├── static/                 built dashboard, served by the Docker image
├── docs/DEPLOYMENT.md      GitHub Pages + Actions guide
├── tests/
│   ├── fixtures/           real upstream captures, committed
│   ├── unit/               parser and merge tests
│   └── regression/         one test per audit finding
├── Dockerfile              multi-stage, non-root
├── docker-compose.yml
├── vercel.json
├── requirements.txt        pinned exactly
├── requirements-scrape.txt fetch-and-parse subset for the batch jobs
└── .env.example
```

---

## Quick start

> **A `&` in the project path used to break `npm run dev` on Windows.** npm runs
> scripts through `cmd.exe`, which treats `&` as a command separator, so a path
> like `PSX & MUFAP Microservice` split and npm looked for vite in the wrong
> place. The npm scripts now call vite through `node` with a relative path, so
> they work whatever the folder is called. Renaming to `psx-mufap-microservice`
> is still worth doing — other tools shell out the same way.

### 1. Backend

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env          # Windows: copy .env.example .env
```

Set an internal token — refresh endpoints stay disabled without one:

```bash
python -c "import secrets; print('INTERNAL_TOKEN=' + secrets.token_urlsafe(32))" >> .env
```

Run it:

```bash
uvicorn app.main:app --reload --port 8000
```

The API is live immediately at <http://localhost:8000/docs>. It has no data yet
— `/ready` returns 503 until the first refresh lands (a few seconds), which the
background scheduler starts on its own.

To force a refresh now:

```bash
curl -X POST http://localhost:8000/internal/refresh \
     -H "X-Internal-Token: YOUR_INTERNAL_TOKEN"
```

### 2. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. Vite proxies `/api` to `http://127.0.0.1:8000`,
so the browser makes same-origin requests and no CORS setup is needed.

Point the proxy elsewhere with `VITE_DEV_API=http://host:port npm run dev`.

### 3. Verify

```bash
curl -s localhost:8000/health
curl -s localhost:8000/ready
curl -s "localhost:8000/api/psx/stocks?limit=3" | python -m json.tool
curl -s "localhost:8000/api/mufap/funds?limit=3&sort_by=return_ytd&ascending=false"
curl -s localhost:8000/api/psx/indices
curl -s localhost:8000/api/psx/market-status
```

---

## Configuration

Every setting is an environment variable. Nothing is read from source.

| Variable | Default | Purpose |
|---|---|---|
| `INTERNAL_TOKEN` | *(unset)* | **Required** to enable refresh endpoints. Unset = routes refuse. |
| `SNAPSHOT_STORE` | auto | `memory` or `redis`. Auto-selects `redis` when Upstash vars are present. |
| `UPSTASH_REDIS_REST_URL` | — | Upstash REST URL (serverless only) |
| `UPSTASH_REDIS_REST_TOKEN` | — | Upstash REST token (serverless only) |
| `SCHEDULER_ENABLED` | `true` for memory | Background refresh loop. Off on serverless. |
| `PSX_INTERVAL_OPEN_SECONDS` | `90` | Refresh cadence while PSX is trading |
| `PSX_INTERVAL_CLOSED_SECONDS` | `1800` | Cadence when it is closed |
| `MUFAP_INTERVAL_SECONDS` | `3600` | NAV publishes once per business day |
| `MUFAP_HTTP_CLIENT` | `curl_cffi` | `curl_cffi` or `httpx` — see [MUFAP access](#mufap-access) |
| `PSX_MIN_ROWS` | `50` | Reject a batch below this |
| `MUFAP_MIN_ROWS` | `100` | Reject a batch below this |
| `MAX_ROW_DROP_RATIO` | `0.5` | Reject a batch that collapses by more than this |
| `PSX_STALE_AFTER_SECONDS` | `600` | Snapshot older than this reports `stale` |
| `MUFAP_STALE_AFTER_SECONDS` | `86400` | Same, for funds |
| `CORS_ORIGINS` | `*` | Comma-separated. Use explicit origins in production. |
| `CORS_ALLOW_CREDENTIALS` | `false` | Ignored while origins is `*` |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per client IP; `0` disables |
| `HTTP_TIMEOUT_SECONDS` | `20` | Per upstream request |
| `HTTP_MAX_RETRIES` | `2` | Bounded; worst case is 3 attempts |
| `LOG_FORMAT` | `json` | `json` or `text` |
| `LOG_LEVEL` | `INFO` | |
| `SERVE_STATIC` | `true` | Mount `static/` at `/` |
| `PORT` | `8000` | |

---

## Docker

```bash
docker compose up -d --build     # build and start
docker compose logs -f           # follow logs
docker compose ps                # status, including health
docker compose down              # stop
```

Or without compose:

```bash
docker build -t pk-finance-unified .
docker run -d --name pk-finance -p 8000:8000 \
  -e INTERNAL_TOKEN=your-token \
  -e ENVIRONMENT=production \
  pk-finance-unified

docker logs -f pk-finance
docker exec pk-finance curl -s localhost:8000/health
docker stop pk-finance && docker rm pk-finance
```

The image runs as a non-root user (`appuser`, uid 10001), installs only pinned
wheels, and its healthcheck targets `/health` (liveness) — not data
availability, so an upstream outage cannot trigger a restart loop.

To rebuild the dashboard into the image:

```bash
cd frontend && npm install && npm run build && cd ..
cp -r frontend/dist/* static/
docker compose up -d --build
```

---

## Scheduling and reliability

> **The short version.** GitHub's `schedule` event is best effort and will not
> hold a timetable. This service therefore treats it as a backstop and takes its
> punctuality from an external trigger. If you only read one section before
> depending on this API, read this one.

### What GitHub actually delivers

Measured on this repository over five consecutive working days, against crons
asking for 12:00 UTC (PSX) and seven hourly fires 13:00–19:00 UTC (MUFAP):

| Workflow | Asked for | GitHub delivered |
|---|---|---|
| PSX | 12:00 UTC | 16:17, 17:03, 16:49, 16:50, 18:11 — **4–6 hours late, every day** |
| MUFAP | 7 fires/day | **1–2 a day**; the other five were never created |

Nothing failed. Nothing was cancelled. The runs simply did not happen. This is
[documented GitHub behaviour](https://docs.github.com/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule)
— scheduled runs are delayed or dropped under load — and it cannot be fixed
from inside the repository.

### The architecture that works anyway

```
  ┌──────────────────────┐   fires within seconds
  │  external scheduler  │───────────────┐          ← the punctual path
  │  (Cloudflare cron)   │               │
  └──────────────────────┘               ▼
                             POST /repos/…/dispatches
  ┌──────────────────────┐               │          ← the manual path
  │  dashboard / curl    │───────────────┤
  └──────────────────────┘               ▼
                                  Refresh on demand ──┐
  ┌──────────────────────┐                            │
  │  GitHub cron         │──→ dense fires ──→ due? ───┤   ← the backstop
  │  (best effort)       │        │           no→exit │
  └──────────────────────┘        └── yes ────────────┤
                                                      ▼
                                        scrape → validate → gate
                                                      │
                                     service-data (one orphan commit)
                                                      │
                                     static API + dashboard on Pages
```

**1. External trigger — the punctual path.** `repository_dispatch` starts a run
within seconds of the POST. A Cloudflare Worker on cron triggers calls it at the
real times. Ships in [`deploy/cloudflare-worker/`](deploy/cloudflare-worker/):
free tier, twelve invocations a working day, one outbound request each.

**2. Dense cron — the backstop.** The repository asks for far more fires than
the data needs, so that whatever fraction GitHub honours still lands near the
intended times.

| | Target | Cron asks for | Minimum interval |
|---|---|---|---|
| PSX | 17:00 PKT, working days | `0 12-17 * * 1-5` (6 fires) | 600 min |
| MUFAP | 18:00–00:00 PKT hourly, working days | `0,30 13-19 * * 1-5` (14 fires) | 45 min |

**3. Cheap refusal.** Every run checks how long ago a fetch actually succeeded
*before* installing anything, using the runner's preinstalled Python and the
snapshot it just checked out. A fire that is not due exits in seconds. Fourteen
fires therefore cost fourteen short jobs — not fourteen scrapes of someone
else's website.

That check keys off **when we last fetched**, never off whether the data *looks*
current. Those are different questions, and the difference matters: a MUFAP
correction issued at 22:00 to a NAV struck at 18:00 leaves the data looking
perfectly current while being wrong. A dispatch bypasses the check entirely.

**4. No cross-blocking.** PSX and MUFAP hold separate concurrency groups, so a
slow run in one can never queue behind or cancel the other. Because they can now
genuinely overlap, `publish-data.sh` pushes with `--force-with-lease` and retries:
on rejection it re-reads the branch, re-takes the other domain's files and pushes
again. Verified against a simulated race — both domains survive, and the branch
stays at exactly one commit.

**5. Lateness is published, not hidden.** Every freshness block carries
`overdue_seconds` and `on_schedule`. Alarm on those rather than reimplementing
the schedule:

```bash
curl -s https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/freshness.json \
  | jq '.datasets | to_entries[] | select(.value.on_schedule == false)'
```

It is weekend-aware: a Friday snapshot read on Sunday is on schedule, because
the next run genuinely is not due until Monday.

### Refresh on demand

The same endpoint the dashboard's **Refresh now** button uses. **No credential
required.**

```bash
curl -X POST https://pk-finance-cron.pk-microservice.workers.dev/v1/refresh   -H "Content-Type: application/json"   -d '{"domain":"mufap"}'
```

`domain` is `psx`, `mufap` or `both`.

| Status | Meaning |
|---|---|
| `200` | Dispatched. The body names what ran and how to follow it. |
| `429` | That data was refreshed too recently to have changed. `Retry-After` says how long. |
| `502` | GitHub refused the dispatch — usually an expired token on the worker. |

A static site cannot keep a secret, so the endpoint is open and protects itself
by refusing pointless work instead: it reads the published freshness file and
declines if the source cannot have published anything new. PSX is capped at one
refresh per four hours — it publishes one closing board per trading day — and
MUFAP at one per twenty minutes. Worst case under sustained abuse is 6 + 72
runs a day; a normal caller is never refused.

To override the throttle, run the workflow from the
[Actions tab](https://github.com/Sohaib-Sarwar/PSX-MUFAP-MicroService/actions/workflows/refresh.yml),
where GitHub has already authenticated you.

**Following a run needs no credential either.** The repository is public, so
the response's `track` block points at endpoints readable anonymously — which
is how the dashboard shows the real run rather than a timer:

```bash
curl -s "https://api.github.com/repos/Sohaib-Sarwar/PSX-MUFAP-MicroService/actions/runs?event=repository_dispatch&per_page=1"
```

Poll [`freshness.json`](https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/freshness.json)
until `fetched_at` moves; that, not the run turning green, is when the CDN is
serving new bytes. Typical end to end: **110 seconds**.

**The only credential in the system** is a GitHub fine-grained PAT with
`Contents: Read and write`, held as a Cloudflare secret on the worker. It never
reaches a browser, never appears in a response, and is never logged.

### Storage

`service-data` is rewritten as a **single orphan commit** every run, so the
branch costs what the current snapshot costs — about 670 KB — however many times
a day it is replaced. Refreshing more often does not make the repository grow.

---

## Public API — live now, no key required

**Base URL** — `https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api`

Every path below is a plain `GET` returning JSON. No key, no quota, no rate
limit: the files sit on GitHub's CDN, so a request costs nothing and never
touches PSX or MUFAP. **1,463 files across 31 endpoint shapes.**

| What a consumer needs | Value |
|---|---|
| CORS | `Access-Control-Allow-Origin: *` — callable from any browser origin |
| Compression | `Content-Encoding: gzip` — `mufap/funds.json` is 334 KB raw, **~36 KB on the wire** |
| Caching | `Cache-Control: max-age=600`, plus `ETag` and `Last-Modified` |
| Auth | None. `GET` only |
| Refresh | PSX 17:00 PKT on working days · MUFAP hourly 18:00–00:00 PKT on working days |

### Start here

| Path | Returns |
|---|---|
| [`freshness.json`](https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/freshness.json) | **Poll this.** Under 1 KB: when every dataset was fetched, what the source dates it, when the next run is due, how many records. Check it before re-downloading anything. |
| [`index.json`](https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/index.json) | **The catalog** — every endpoint, every field, both schedules. Generated by the same code that writes the data, so it cannot drift. |
| [`search.json`](https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/search.json) | **Search index** over both domains, 1,299 entries. Each hit carries the `endpoint` holding its full record. |
| [`/health.json`](https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/health.json) · [`/ready.json`](https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/ready.json) | Liveness and per-dataset readiness. At the site root, not under `/api`. |

### PSX equities — `api/psx/`

| Path | Returns | Rows |
|---|---|---|
| `psx/stocks.json` | **Every listed instrument** with price, move, market cap, P/E, dividend yield, free float, 30-day average volume and index memberships | 747 |
| `psx/stocks/{symbol}.json` | **One instrument**, lower-case ticker — `psx/stocks/ogdc.json`. ~700 bytes | 1 |
| `psx/stocks/summary.json` | Market breadth from the exchange's own session header | — |
| `psx/stocks/quoted.json` | Only rows carrying today's OHLC and session volume | ~120 |
| `psx/stocks/gainers.json` · `losers.json` · `active.json` | Movers, and most active by 30-day average volume | 50 each |
| `psx/stocks/top/market-cap.json` | Largest companies by market value | 50 |
| `psx/stocks/top/dividend-yield.json` | Highest trailing dividend yield | 50 |
| `psx/stocks/top/pe-ratio.json` | Lowest positive trailing P/E | 50 |
| `psx/stocks/top/yearly-gainers.json` | Best twelve-month performance | 50 |
| `psx/sectors.json` | Per-sector breadth and combined market cap, each with its `slug` | 38 |
| `psx/sectors/{slug}.json` | **Instruments in one sector** — `psx/sectors/commercial-banks.json` | per sector |
| `psx/indices.json` | Index board — KSE100, KSE30, KMI30, ALLSHR and the rest | 18 |
| `psx/indices/{name}.json` | **Constituents of one index** — `psx/indices/kse100.json` returns exactly 100 | per index |
| `psx/market-status.json` | `open` / `closed`, with every market segment's state | — |
| `psx/session.json` | Session totals per segment: Regular, Futures, Bills & Bonds… | 14 |

### MUFAP mutual funds — `api/mufap/`

| Path | Returns | Rows |
|---|---|---|
| `mufap/funds.json` | **Every fund** — NAV, offer/repurchase, all three loads, rating, trustee, AMC, 11 return periods | 552 |
| `mufap/fund/{slug}.json` | **One fund** — `mufap/fund/786-islamic-money-market-fund.json` | 1 |
| `mufap/funds/categories.json` | Every category with fund count and `slug` | 35 |
| `mufap/funds/category/{slug}.json` | **Funds in one category** — `mufap/funds/category/money-market.json` | per category |
| `mufap/funds/amcs.json` | Every AMC with fund count, categories and `slug` | 25 |
| `mufap/funds/amc/{slug}.json` | **Funds from one AMC** | per AMC |
| `mufap/funds/ratings.json` · `trustees.json` | Rating and trustee distributions | 6 · 2 |
| `mufap/funds/stats.json` | Industry aggregates — NAV and YTD mean/median/min/max | — |
| `mufap/funds/top/{period}.json` | **Best performers** over one period | 50 |
| `mufap/funds/bottom/{period}.json` | **Worst performers** over one period | 50 |

`{period}` is one of `ytd` `mtd` `d1` `d15` `d30` `d90` `d180` `d270` `d365`
`y2` `y3`.

### Knowing whether you have the latest price

Every response carries a `freshness` block, and **`fetched_at` is the field that
answers it**:

```json
{
  "count": 552,
  "freshness": {
    "state": "fresh",
    "fetched_at": "2026-09-25T01:36:06+05:00",
    "data_as_of": "2026-09-25",
    "stale_after_seconds": 7200,
    "next_refresh_at": "2026-09-25T18:00:00+05:00",
    "record_count": 552
  },
  "data": []
}
```

- **`fetched_at`** — when this service actually pulled from MUFAP or PSX.
- **`data_as_of`** — what the *source* dates the figures: the NAV validity date
  for MUFAP, the session stamp PSX printed on the board for equities. Not the
  same as `fetched_at`, and it is the one that identifies the session.
- **`state`** — `fresh`, `stale`, `degraded` or `unavailable`. `degraded` means
  the last refresh failed and you are reading the previous good snapshot: real
  data, just not new.

`age_seconds` is measured when the file is written and **cannot tick on a static
host**. Compute it yourself:

```js
const age = (Date.now() - Date.parse(body.freshness.fetched_at)) / 1000
const stale = age > body.freshness.stale_after_seconds
```

`stale_after_seconds` comes from the publication schedule, so it already
accounts for weekends — a Friday snapshot read on Sunday is `fresh`, not stale.

**Every scheduled run fetches and replaces.** There is no "the date already
looks current" shortcut, so a MUFAP correction issued at 22:00 to a NAV struck
at 18:00 is collected by the 22:00 run.

### Searching

A static host cannot answer `?q=` — the query is not known when the file is
written. `search.json` publishes what a match needs instead, and each hit points
at its own record:

```js
const BASE = 'https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api'
const index = await fetch(`${BASE}/search.json`).then((r) => r.json())

const hits = index.data.filter((row) =>
  [row.id, row.label, row.issuer].some((f) => f?.toLowerCase().includes('habib'))
)
// { type:'stock', id:'HBL', label:'Habib Bank Limited',
//   group:'COMMERCIAL BANKS', active:true, endpoint:'psx/stocks/hbl.json' }

const full = await fetch(`${BASE}/${hits[0].endpoint}`).then((r) => r.json())
console.log(full.data.current, full.freshness.fetched_at)
```

### Using it from another project

```bash
# Is there anything new? — under 1 KB
curl -s https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/freshness.json | jq '.datasets["mufap.funds"]'

# One instrument
curl -s https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/psx/stocks/ogdc.json | jq '.data | {symbol, current, change_pct, market_cap}'
```

```python
import urllib.request, json

BASE = "https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api"

def get(path):
    with urllib.request.urlopen(f"{BASE}/{path}") as response:
        return json.load(response)

# Poll cheaply, then fetch only if the fetch time moved
state = get("freshness.json")["datasets"]["mufap.funds"]
print("last fetched:", state["fetched_at"], "| next run:", state["next_refresh_at"])

money_market = get("mufap/funds/category/money-market.json")
ranked = sorted(money_market["data"],
                key=lambda f: f["returns"]["ytd"] or 0, reverse=True)
for fund in ranked[:5]:
    print(f'{fund["returns"]["ytd"]:6.2f}%  {fund["fund_name"]}')
```

```python
import pandas as pd

BASE = "https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api"
stocks = pd.json_normalize(pd.read_json(f"{BASE}/psx/stocks.json")["data"])
print(stocks.nlargest(10, "market_cap")[["symbol", "name", "current", "pe_ratio"]])
```

### What PSX changed on 2026-09-24

PSX withdrew its bulk quote feeds. `/symbols`, `/market-watch` and `/timeseries`
now answer **403 with an empty body** to an XHR client and 404 to anything else —
verified from a full browser TLS profile with a warmed session, so it is a
deliberate lockdown, not a fingerprint or a rate limit.

The pipeline was rebuilt on `/screener`, `/trading-panel` and `/indices`, which
are still server-rendered. What that means for a consumer:

| Field | Before | Now |
|---|---|---|
| `name`, `sector`, `current`, `change_pct` | ✅ | ✅ still every row |
| `market_cap`, `pe_ratio`, `dividend_yield`, `free_float`, `volume_30d_avg`, `change_1y_pct` | ✗ | ✅ **new**, every row |
| `ldcp`, `open`, `high`, `low`, `volume` | every traded row | only where `has_quote` is `true` — fetched per instrument for the largest ~120 |
| `traded` | traded in the session | **now means "priced in the screener"**. For the real traded count use `psx/stocks/summary.json` |
| Intraday series | `/api/psx/series/{symbol}` | removed — the upstream is gone |

`psx/stocks/summary.json` takes advancing/declining/unchanged, total trades,
volume and value straight from the exchange's session header rather than
counting the universe, because the screener's percentages cannot distinguish
"closed unchanged" from "did not trade".

**Attribution.** Data originates from the Pakistan Stock Exchange
(`dps.psx.com.pk`) and the Mutual Funds Association of Pakistan
(`mufap.com.pk`), fetched within what each site's `robots.txt` permits. This
service reformats and republishes it; it does not own it, does not warrant it,
and is not investment advice.

---

## API reference — self-hosted service

The sections below describe the **live FastAPI service** (Docker or Vercel),
where filtering and pagination happen server-side through query parameters. The
static API above is the same data with those queries precomputed into paths.

Interactive docs at `/docs`. Every list endpoint returns the same envelope.

### System

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness. 200 once the process is up. |
| `GET` | `/ready` | Readiness. 503 until a dataset has data. |
| `GET` | `/metrics` | Prometheus text format |
| `GET` | `/api` | Service index |
| `POST` | `/internal/refresh` | Refresh all datasets. **Requires `X-Internal-Token`.** |

### PSX — `/api/psx`

| Method | Path | Notes |
|---|---|---|
| `GET` | `/stocks` | Full universe, filtered and paginated |
| `GET` | `/stocks/search?q=` | Match symbol or company name |
| `GET` | `/stocks/gainers?limit=` | Top movers up |
| `GET` | `/stocks/losers?limit=` | Top movers down |
| `GET` | `/stocks/active?limit=` | By traded volume |
| `GET` | `/stocks/summary` | Market breadth and totals |
| `GET` | `/stocks/{symbol}` | One instrument |
| `GET` | `/indices` | 18 indices with high/low |
| `GET` | `/series/{symbol}?kind=int\|eod` | Price series |
| `GET` | `/market-status` | Open / closed, derived from the tick feed |
| `POST` | `/refresh` | **Requires `X-Internal-Token`** |

`/stocks` accepts: `limit` `offset` `sort_by` `ascending` `search` `sector`
`traded_only` `is_etf` `is_debt` `min_price` `max_price` `min_volume`
`min_change_pct` `max_change_pct`.

Sortable: `symbol` `name` `sector` `ldcp` `open` `high` `low` `current`
`change` `change_pct` `volume`. An unknown field returns **400**, not silently
unsorted data.

### MUFAP — `/api/mufap`

| Method | Path | Notes |
|---|---|---|
| `GET` | `/funds` | All funds, filtered and paginated |
| `GET` | `/funds/search?q=` | Match fund name or AMC |
| `GET` | `/funds/categories` | Categories with counts |
| `GET` | `/funds/amcs` | Asset managers with counts |
| `GET` | `/funds/top?period=ytd` | Best performers over a period |
| `GET` | `/funds/top-nav?limit=` | Highest NAV |
| `GET` | `/funds/stats` | Aggregate NAV and return statistics |
| `GET` | `/funds/{fund_name}` | One fund (all sub-funds if VPS) |
| `POST` | `/refresh` | **Requires `X-Internal-Token`** |

`/funds` accepts: `limit` `offset` `sort_by` `ascending` `search` `category`
`sector` `amc` `trustee` `rating` `min_nav` `max_nav` `min_ytd`.

Return periods: `ytd` `mtd` `d1` `d15` `d30` `d90` `d180` `d270` `d365` `y2` `y3`.

### Examples

```bash
BASE=http://localhost:8000

curl "$BASE/api/psx/stocks?limit=10&sort_by=change_pct&ascending=false"
curl "$BASE/api/psx/stocks?sector=REFINERY&traded_only=true"
curl "$BASE/api/psx/stocks?is_etf=true&traded_only=false"
curl "$BASE/api/psx/stocks/CNERGY"
curl "$BASE/api/psx/series/KSE100?kind=eod"

curl "$BASE/api/mufap/funds?category=Money%20Market&sort_by=return_ytd&ascending=false"
curl "$BASE/api/mufap/funds?amc=ABL&min_ytd=10"
curl "$BASE/api/mufap/funds/top?period=y3&limit=10"
curl "$BASE/api/mufap/funds/ABL%20Cash%20Fund"
```

### Response shape

```json
{
  "count": 10,
  "total_filtered": 500,
  "total": 1075,
  "offset": 0,
  "limit": 10,
  "freshness": {
    "state": "stale",
    "data_as_of": "2026-09-11T16:50:00+05:00",
    "fetched_at": "2026-09-13T21:22:51+05:00",
    "age_seconds": 1.1,
    "record_count": 1075,
    "market_status": "closed"
  },
  "data": [ … ]
}
```

Errors never leak internals:

```json
{ "error": { "code": "not_found",
             "message": "Symbol 'XYZ' is not listed on PSX.",
             "request_id": "a1b2c3d4e5f6" } }
```

---

## Freshness contract

Every response says how current the data actually is.

| State | Meaning |
|---|---|
| `fresh` | Fetched recently and the batch passed validation |
| `stale` | Older than the configured window — usually the market is closed |
| `degraded` | The last refresh failed; you are seeing the previous good data |
| `unavailable` | No usable data at all (endpoint returns 503) |

`data_as_of` is **when the market last traded** or the NAV validity date.
`fetched_at` is when we looked. They are different numbers, and conflating them
is what makes weekend data look live.

MUFAP funds do not all publish on the same day — a live sample spanned Sep 09 to
Sep 14 — so each record keeps its own `validity_date` and the batch reports the
newest.

---

## Live-data strategy

**Refresh cadence is adaptive, not a fixed interval.** PSX trades roughly 31 of
the 168 hours in a week, and MUFAP strikes NAV once per business day.

| Dataset | Market open | Market closed |
|---|---|---|
| PSX stocks + indices | every 90s | every 30 min |
| MUFAP funds | hourly until the validity date advances, then idle | same |

**Market status is derived, not hardcoded.** The newest tick in
`/timeseries/int/KSE100` says when the market last moved: minutes old means
open, days old means closed. This stays correct across weekends, public
holidays and Ramadan hours with no calendar to maintain. (PSX's `/calendar`
page is an AGM calendar and does not carry trading days.)

**Conditional requests are not available.** `/market-watch` sends
`Cache-Control: no-cache, private, no-store` with no `ETag` and no
`Last-Modified`, so `If-None-Match` strategies are impossible for that page.

### MUFAP access

MUFAP's `robots.txt` permits this use outright:

```
User-agent: *
Content-Signal: search=yes, ai-train=no, use=reference
Allow: /
```

but the site sits behind Cloudflare bot management that rejects the request on
its **TLS fingerprint**, not on anything in the HTTP layer. Measured:

| Client | Result |
|---|---|
| httpx, minimal headers | 403 |
| httpx, full Chrome header set | 403 |
| httpx, tuned TLS cipher list | 403 |
| curl_cffi, browser TLS profile | **200** (1.27 MB) |

So `curl_cffi` is the default MUFAP client. Set `MUFAP_HTTP_CLIENT=httpx` to
opt out; the funds endpoints will then report `degraded` or `unavailable`.
PSX never uses this path.

If MUFAP blocks the deployment anyway, the service keeps serving the last good
snapshot marked `degraded` and logs `mufap_challenged` — it does not go down.
The durable fix is a data agreement with MUFAP.

---

## Caching

| Dataset | Retained | Bound |
|---|---|---|
| PSX stocks | current + last known good | 2 × ~1,075 rows |
| PSX indices | current + last known good | 2 × 18 rows |
| MUFAP funds | current + last known good | 2 × ~546 rows |
| PSX series | not cached — proxied per request | — |

Two snapshots per dataset is the entire memory bound. Concurrent refreshes are
**coalesced**: a burst of requests produces one upstream fetch, not a burst of
them.

---

## Error handling

Every upstream call has an explicit timeout, a bounded retry count and
exponential backoff capped at 4s. Worst case for one fetch is three attempts.

A failed refresh never takes the service down:

1. The exception is logged with the dataset and reason.
2. `refresh_failed_total` increments.
3. The previous good snapshot keeps serving, marked `degraded`.
4. If there is no previous snapshot, endpoints return **503** with a reason.

A batch that parses but looks wrong is refused before publication — see
`PSX_MIN_ROWS`, `MUFAP_MIN_ROWS` and `MAX_ROW_DROP_RATIO`. Individual records
that are impossible (negative price, low above high) are dropped and counted;
records that are merely unusual (an illiquid stock quoting outside its day
range) are annotated with `anomalies` and kept.

---

## Testing

```bash
pip install -r requirements-dev.txt

pytest                                  # everything
pytest tests/unit -v                    # parsers and merges
pytest tests/regression -v              # one test per audit finding
pytest -k cnergy -v                     # a single case
pytest --tb=long -x                     # stop at the first failure
```

Fixtures in `tests/fixtures/` are verbatim upstream captures, so tests are
deterministic and need no network. Assertions are on **exact values** for known
rows, because a shape-only test passes just as happily against a parser that
returns plausible but wrong numbers.

Current: **54 passed**.

---

## Health checks

| Endpoint | Purpose | Point this at |
|---|---|---|
| `/health` | Liveness — is the process up? | container healthcheck, restart policy |
| `/ready` | Readiness — is there usable data? | load balancer, traffic gate |

Keeping them separate matters: tying liveness to data means an upstream outage
restarts a perfectly healthy process, which cannot help.

```bash
curl -s localhost:8000/health
curl -sI localhost:8000/ready | head -1     # 200 ready, 503 warming
```

---

## Monitoring

`/metrics` in Prometheus text format:

| Metric | Type | Meaning |
|---|---|---|
| `refresh_success_total{dataset}` | counter | successful refreshes |
| `refresh_failed_total{dataset}` | counter | failed refreshes |
| `refresh_coalesced_total{dataset}` | counter | requests merged into an in-flight fetch |
| `batch_rejected_total{dataset}` | counter | **batches refused by the publication gate** |
| `records_rejected_total{dataset}` | counter | impossible records dropped |
| `records_flagged_total{dataset}` | counter | records kept but annotated |
| `snapshot_records{dataset}` | gauge | rows in the current snapshot |

Worth alerting on: `batch_rejected_total` increasing (upstream markup probably
changed), `refresh_failed_total` increasing, and any dataset whose freshness
state sits at `degraded`.

Logs are structured JSON with a `request_id` on every line, so one request can
be followed end to end. Tokens and credentials are redacted by the formatter.

---

## Deploy to GitHub Pages

The default deployment, and the one with no server in it. GitHub Actions runs
the scrapers on a schedule; GitHub Pages serves the dashboard and the entire
read API as static files on a CDN.

Every read endpoint of the live service is a pure function of one snapshot, so
each one is precomputed at publish time. What a query parameter selects on the
live API is selected here by path:

```
GET /api/psx/stocks/gainers?limit=50   →   /api/psx/stocks/gainers.json
```

Response bodies keep the live service's envelope — `count`, `total`,
`freshness`, `data` — so a consumer written against one works against the other.

### Setup

1. `git push -u origin unified-service`
2. **Settings → General → Default branch → `unified-service`.** GitHub runs
   `schedule` triggers only from the default branch; leave it on `main` and no
   cron will ever fire, silently.
3. **Settings → Pages → Source → _GitHub Actions_.**
4. **Actions → "PSX — daily close" → Run workflow**, then the same for
   "MUFAP — evening NAV", to seed the data.

The site is then at `https://<owner>.github.io/<repo>/`, with the API under
`/api/` and a full reference page at `#/api`.

### Schedule

| Workflow | Pakistan time | UTC cron |
| --- | --- | --- |
| PSX | 17:10, Mon–Fri — after the close | `10 12 * * 1-5` |
| MUFAP | 18:05 → 00:05 hourly, Mon–Fri | `5 13-19 * * 1-5` |

PSX publishes one closing board per trading day, so it is fetched once. MUFAP
strikes NAV once per business day but at no fixed hour, so the evening is swept —
and each run exits without opening a connection if the published validity date
already covers the current session, which typically costs one or two fetches out
of a possible seven.

Full setup, resource budget, policy notes and troubleshooting:
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

---

## Deploy to Vercel

Vercel's Python runtime is serverless: process memory does not survive between
invocations and there is no place for a background loop. So the snapshot store
moves to Upstash Redis and Vercel Cron drives refreshes.

### 1. Create an Upstash Redis database

1. Sign up at <https://upstash.com> and create a Redis database.
2. Pick the region closest to your Vercel region.
3. From **REST API**, copy `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.

### 2. Generate secrets

```bash
python -c "import secrets; print('INTERNAL_TOKEN =', secrets.token_urlsafe(32))"
python -c "import secrets; print('CRON_SECRET    =', secrets.token_urlsafe(32))"
```

### 3. Import the repository

In the Vercel dashboard: **Add New → Project → Import** your repo, then set
**Production Branch** to `unified-service` under *Settings → Git*.

Vercel reads `vercel.json`, so the build command and output directory are
already configured — leave the framework preset as **Other**.

### 4. Set environment variables

*Settings → Environment Variables*, for **Production** and **Preview**:

| Name | Value |
|---|---|
| `UPSTASH_REDIS_REST_URL` | from Upstash |
| `UPSTASH_REDIS_REST_TOKEN` | from Upstash |
| `INTERNAL_TOKEN` | generated above |
| `CRON_SECRET` | generated above |
| `SNAPSHOT_STORE` | `redis` |
| `SCHEDULER_ENABLED` | `false` |
| `SERVE_STATIC` | `false` |
| `ENVIRONMENT` | `production` |
| `CORS_ORIGINS` | your dashboard origin, e.g. `https://your-app.vercel.app` |

### 5. Deploy

```bash
npm i -g vercel
vercel login
vercel link
vercel --prod
```

Or just push to `unified-service` — Vercel builds on push.

### 6. Prime the cache

Cron runs every 10 minutes (`vercel.json` → `crons`), but the first deploy has
no data. Trigger one refresh immediately:

```bash
curl -X POST https://your-app.vercel.app/internal/refresh \
     -H "X-Internal-Token: YOUR_INTERNAL_TOKEN"
```

### 7. Verify

```bash
APP=https://your-app.vercel.app
curl -s $APP/health
curl -s $APP/ready
curl -s "$APP/api/psx/stocks?limit=3"
curl -s "$APP/api/mufap/funds?limit=3"
open $APP                     # the dashboard
```

### Vercel notes

- **Cron requires a Pro plan** for schedules more frequent than once a day. On
  Hobby, change the schedule in `vercel.json` to `0 * * * *` or refresh via
  `/internal/refresh` from an external scheduler.
- Cold starts add roughly 1–2s to the first request after idle.
- Each response costs a Redis round trip, typically 50–200ms — the trade for
  not having a persistent process.
- The dashboard is served as static output; the API is a single serverless
  function. Both share the domain, so the frontend needs no `VITE_API_BASE`.
- `maxDuration` is 60s in `vercel.json`; a cold refresh of both domains takes
  roughly 10–15s.

---

## Deploy with Docker

Any host that runs a container — Railway, Render, Fly, a VPS.

```bash
docker build -t pk-finance-unified .
docker run -d --restart unless-stopped -p 8000:8000 \
  -e ENVIRONMENT=production \
  -e INTERNAL_TOKEN=your-token \
  -e CORS_ORIGINS=https://your-dashboard.example \
  -e LOG_FORMAT=json \
  --memory 384m --cpus 1 \
  pk-finance-unified
```

This is the better fit if you want sub-millisecond responses and minimum
upstream load: the process holds its snapshots in memory and refreshes itself.

**Railway:** point the service at this branch, leave the Dockerfile detected,
set the same environment variables, and Railway supplies `PORT` automatically.

---

## Troubleshooting

**`/ready` returns 503 forever**
Check the logs for `refresh_failed` or `batch_rejected`. Force a refresh:
`curl -X POST localhost:8000/internal/refresh -H "X-Internal-Token: …"`.

**`/internal/refresh` returns 401**
`INTERNAL_TOKEN` is unset or does not match. Unset means the route refuses by
design rather than sitting open.

**MUFAP endpoints report `degraded` or `unavailable`**
Cloudflare challenged the fetch. Confirm `MUFAP_HTTP_CLIENT=curl_cffi` and that
`curl_cffi` installed. Look for `mufap_challenged` in the logs. PSX is
unaffected.

**`batch_rejected` in the logs**
A parse returned far fewer rows than the previous good snapshot — usually
upstream markup changed. The old data keeps serving. Run
`pytest tests/unit -v`; the parser tests fail with the changed header row.

**`ColumnMapError: upstream header changed`**
A required column disappeared from the upstream table. This is intentional —
the parser raises rather than guessing positions. Update the column spec in
`app/psx/parsers.py` or `app/mufap/parsers.py`.

**`npm run dev` fails with `'MUFAP' is not recognized` / `MODULE_NOT_FOUND`**
An older `package.json` called `vite` directly, and npm runs scripts through
`cmd.exe`, which splits the path at the `&` in the folder name. The scripts now
invoke `node ./node_modules/vite/bin/vite.js`, which has no absolute path for
cmd to mis-split. If you still hit it, run `npm install` again so the scripts
are picked up, or rename the folder to `psx-mufap-microservice`.

**Frontend shows no data locally**
The backend must be running on the port Vite proxies to (8000 by default).
Check `curl localhost:8000/ready`, or set `VITE_DEV_API`.

**CORS errors in the browser**
Set `CORS_ORIGINS` to your dashboard's exact origin. Locally, use the Vite
proxy instead — it makes requests same-origin.

---

## Contributing

```bash
pip install -r requirements-dev.txt
pytest
```

Anything that changes a parser needs a test asserting exact values against a
fixture. Add the fixture to `tests/fixtures/` as a verbatim upstream capture.

## License

MIT — see [LICENSE](LICENSE).

Market data belongs to PSX and MUFAP respectively. Check their terms before
redistributing it.
