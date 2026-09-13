# PK Finance — Unified Service

Pakistan market data as one API: **PSX equities** and **MUFAP mutual funds**, plus a React dashboard.

| | |
|---|---|
| **Domains** | `/api/psx/*` · `/api/mufap/*` |
| **Coverage** | 1,020 listed PSX instruments · 546 mutual funds |
| **Dashboard** | served at `/` (Docker) or by Vercel (serverless) |
| **Docs** | `/docs` (Swagger) · `/redoc` |

Sibling branches: [`pk-micro-service`](../../tree/pk-micro-service) (PSX only) · [`mufap-service`](../../tree/mufap-service) (MUFAP only).

---

## Contents

- [Overview](#overview) · [Architecture](#architecture) · [Project structure](#project-structure)
- [Quick start](#quick-start) · [Configuration](#configuration) · [Docker](#docker)
- [API reference](#api-reference) · [Freshness contract](#freshness-contract)
- [Live-data strategy](#live-data-strategy) · [Caching](#caching) · [Error handling](#error-handling)
- [Testing](#testing) · [Health checks](#health-checks) · [Monitoring](#monitoring)
- [Deploy to Vercel](#deploy-to-vercel) · [Deploy with Docker](#deploy-with-docker)
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
  Vercel Cron → POST /internal/refresh ─────────────┘

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

### Project structure

```
├── app/                    application package (see above)
├── api/index.py            Vercel serverless entrypoint + cron route
├── frontend/               React 19 + Vite 6 dashboard (source)
├── static/                 built dashboard, served by the Docker image
├── tests/
│   ├── fixtures/           real upstream captures, committed
│   ├── unit/               parser and merge tests
│   └── regression/         one test per audit finding
├── Dockerfile              multi-stage, non-root
├── docker-compose.yml
├── vercel.json
├── requirements.txt        pinned exactly
└── .env.example
```

---

## Quick start

> **Rename the project folder first if it contains spaces or `&`.**
> `npm run build` fails on Windows inside a path like `PSX & MUFAP Microservice`
> — the `&` truncates the resolved path. Use `psx-mufap-microservice`.

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

## API reference

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

**`npm run build` fails with `MODULE_NOT_FOUND`**
The project path contains a space or `&`. Rename the folder to
`psx-mufap-microservice`. As a stopgap:
`node ./node_modules/vite/bin/vite.js build`.

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
