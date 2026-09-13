# PSX Market Data Service

Pakistan Stock Exchange market data as a REST API. **PSX only** — this branch
contains no MUFAP code, configuration, dependencies or environment variables,
and clones and runs on its own.

| | |
|---|---|
| **Coverage** | 1,020 listed instruments · ~500 trading on a given session · 18 indices |
| **Port** | `8000` |
| **Docs** | `/docs` (Swagger) · `/redoc` |

Sibling branches: [`mufap-service`](../../tree/mufap-service) (funds only) ·
[`unified-service`](../../tree/unified-service) (both + dashboard).

---

## Overview

PSX publishes no documented API, so this service reads four public endpoints on
the data portal:

| Source | What we take | Format |
|---|---|---|
| `/symbols` | 1,020 listed instruments: name, sector, ETF/debt flags | JSON |
| `/market-watch` | prices for the ~500 that traded today | HTML table |
| `/indices` | 18 indices with high/low/change | HTML table |
| `/timeseries/{int,eod}/{symbol}` | intraday and end-of-day series | JSON |

`/market-watch` only carries instruments that actually traded, so it is merged
with `/symbols` to give the full universe. The 575 that did not trade come back
with `traded: false` and null prices rather than silently missing — a consumer
can tell "did not trade" from "we failed to fetch it".

### Features

- Full listed universe with company names, sector names and ETF/debt flags
- Market status derived from the live tick feed, not a hardcoded calendar
- Explicit freshness on every response — `fresh` / `stale` / `degraded` / `unavailable`
- Strict header-mapped parser that raises on a changed column rather than guessing
- Publication gate that refuses a collapsed batch instead of overwriting good data
- Adaptive refresh: every 90s while trading, every 30 min when closed
- Intraday and end-of-day price series proxied from the JSON feed

---

## Architecture

```
request path — never blocks on an upstream
  client → API layer → snapshot store → response + freshness envelope

refresh path — background scheduler
  fetch (/symbols + /market-watch + /indices) → parse → validate → publish

app/
├── infra/     config · http · store · parsing · validation · responses
│              errors · security · logging · metrics
├── psx/       parsers · service · router
├── system.py  /health · /ready · /metrics · /internal/refresh
└── main.py    application factory
```

A request handler never touches the network. It reads the current snapshot,
which holds the latest good data plus the previous one — that is the entire
memory bound.

```
├── app/                 application package
├── tests/
│   ├── fixtures/        real upstream captures, committed
│   ├── unit/            parser and merge tests
│   └── regression/      one test per audit finding
├── Dockerfile           multi-stage, non-root
├── docker-compose.yml
├── requirements.txt     pinned exactly
└── .env.example
```

---

## Quick start

> Rename the project folder if it contains spaces or `&` — those break tooling
> on Windows. `psx-market-service` works.

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
```

Refresh endpoints stay disabled without a token:

```bash
python -c "import secrets; print('INTERNAL_TOKEN=' + secrets.token_urlsafe(32))" >> .env
```

Run:

```bash
uvicorn app.main:app --reload --port 8000
```

The API serves immediately at <http://localhost:8000/docs>. `/ready` returns
503 until the first refresh lands, which the scheduler starts on its own.

Force a refresh now:

```bash
curl -X POST http://localhost:8000/internal/refresh \
     -H "X-Internal-Token: YOUR_INTERNAL_TOKEN"
```

### Verify

```bash
curl -s localhost:8000/health
curl -s localhost:8000/ready
curl -s "localhost:8000/api/psx/stocks?limit=3" | python -m json.tool
curl -s localhost:8000/api/psx/indices
curl -s localhost:8000/api/psx/market-status
curl -s localhost:8000/api/psx/stocks/CNERGY
```

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `INTERNAL_TOKEN` | *(unset)* | **Required** to enable refresh endpoints |
| `SCHEDULER_ENABLED` | `true` | Background refresh loop |
| `PSX_INTERVAL_OPEN_SECONDS` | `90` | Cadence while the market trades |
| `PSX_INTERVAL_CLOSED_SECONDS` | `1800` | Cadence when closed |
| `PSX_MIN_ROWS` | `50` | Reject a batch below this |
| `MAX_ROW_DROP_RATIO` | `0.5` | Reject a batch that collapses by more than this |
| `PSX_STALE_AFTER_SECONDS` | `600` | Snapshot older than this reports `stale` |
| `MARKET_OPEN_TICK_WINDOW_SECONDS` | `600` | Tick age that counts as "open" |
| `CORS_ORIGINS` | `*` | Comma-separated; use explicit origins in production |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per client IP; `0` disables |
| `HTTP_TIMEOUT_SECONDS` | `20` | Per upstream request |
| `HTTP_MAX_RETRIES` | `2` | Bounded; worst case 3 attempts |
| `SNAPSHOT_STORE` | `memory` | `memory` or `redis` (Upstash, for serverless) |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | |
| `PORT` | `8000` | |

---

## Docker

```bash
docker compose up -d --build
docker compose logs -f
docker compose ps
docker compose down
```

Without compose:

```bash
docker build -t psx-service .
docker run -d --name psx -p 8000:8000 \
  -e INTERNAL_TOKEN=your-token -e ENVIRONMENT=production \
  psx-service

docker logs -f psx
docker exec psx curl -s localhost:8000/health
docker stop psx && docker rm psx
```

Runs as non-root (`appuser`, uid 10001) with pinned wheels. The healthcheck
targets `/health` (liveness), not data availability, so an upstream outage
cannot trigger a restart loop.

---

## API reference

### System

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness. 200 once the process is up. |
| `GET` | `/ready` | Readiness. 503 until data has loaded. |
| `GET` | `/metrics` | Prometheus text format |
| `GET` | `/api` | Service index |
| `POST` | `/internal/refresh` | **Requires `X-Internal-Token`** |

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

`/stocks` accepts `limit` `offset` `sort_by` `ascending` `search` `sector`
`traded_only` `is_etf` `is_debt` `min_price` `max_price` `min_volume`
`min_change_pct` `max_change_pct`.

Sortable: `symbol` `name` `sector` `ldcp` `open` `high` `low` `current`
`change` `change_pct` `volume`. An unknown field returns **400**, not silently
unsorted data.

```bash
BASE=http://localhost:8000
curl "$BASE/api/psx/stocks?limit=10&sort_by=change_pct&ascending=false"
curl "$BASE/api/psx/stocks?sector=REFINERY"
curl "$BASE/api/psx/stocks?is_etf=true&traded_only=false"
curl "$BASE/api/psx/stocks?min_volume=1000000&sort_by=volume&ascending=false"
curl "$BASE/api/psx/stocks/search?q=habib"
curl "$BASE/api/psx/series/KSE100?kind=eod"
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
  "data": [
    {
      "symbol": "CNERGY", "name": "Cnergyico PK  Limited", "sector": "REFINERY",
      "ldcp": 12.37, "open": 12.21, "high": 13.05, "low": 11.86,
      "current": 12.93, "change": 0.56, "change_pct": 4.53,
      "volume": 104274125, "traded": true,
      "is_etf": false, "is_debt": false,
      "indices": ["ALLSHR", "JSMFI", "KMIALLSHR", "KSE100", "KSE100PR"]
    }
  ]
}
```

---

## Freshness contract

| State | Meaning |
|---|---|
| `fresh` | Fetched recently and the batch passed validation |
| `stale` | Older than the window — usually the market is closed |
| `degraded` | The last refresh failed; you are seeing the previous good data |
| `unavailable` | No usable data at all (503) |

`data_as_of` is **when the market last traded**; `fetched_at` is when we looked.
They are different numbers — conflating them is what makes weekend data look
live.

---

## Live-data strategy

PSX trades roughly 31 of the 168 hours in a week, so a fixed interval spends
most of its requests re-fetching unchanged data.

| Market | Cadence |
|---|---|
| Open | every 90s |
| Closed | every 30 min |

**Market status is derived.** The newest tick in `/timeseries/int/KSE100` says
when the market last moved: minutes old means open, days old means closed. No
trading calendar to maintain, and it stays correct across weekends, public
holidays and Ramadan hours. (PSX's `/calendar` page is an AGM calendar and does
not carry trading days.)

**Conditional requests are unavailable.** `/market-watch` sends
`Cache-Control: no-cache, private, no-store` with no `ETag` and no
`Last-Modified`, so `If-None-Match` strategies are impossible for that page.

`robots.txt` at `psx.com.pk` is `Disallow:` (empty — allow all), with only
`/cgi-bin/` excluded.

---

## Caching

| Dataset | Retained | Bound |
|---|---|---|
| Stocks | current + last known good | 2 × ~1,075 rows |
| Indices | current + last known good | 2 × 18 rows |
| Series | not cached — proxied per request | — |

Concurrent refreshes are **coalesced**: a burst of requests produces one
upstream fetch, not a burst of them.

---

## Error handling

Every upstream call has an explicit timeout, bounded retries and exponential
backoff capped at 4s. Worst case for one fetch is three attempts.

A failed refresh never takes the service down: the previous good snapshot keeps
serving marked `degraded`, the failure is logged and counted, and if there is no
previous snapshot the endpoints return **503** with a reason.

A batch that parses but looks wrong is refused before publication
(`PSX_MIN_ROWS`, `MAX_ROW_DROP_RATIO`). Impossible records — negative price, low
above high — are dropped and counted; merely unusual ones (an illiquid stock
quoting outside its day range) are annotated with `anomalies` and kept.

---

## Testing

```bash
pip install -r requirements-dev.txt

pytest                       # everything
pytest tests/unit -v         # parser and merge tests
pytest tests/regression -v   # one test per audit finding
pytest -k cnergy -v
```

Fixtures are verbatim upstream captures, so tests are deterministic and need no
network. Assertions are on **exact values** for known rows — a shape-only test
passes just as happily against a parser returning plausible but wrong numbers.

Current: **36 passed, 2 skipped** (the skips are MUFAP cases, absent here).

---

## Health checks

| Endpoint | Purpose | Point this at |
|---|---|---|
| `/health` | Liveness | container healthcheck, restart policy |
| `/ready` | Readiness | load balancer, traffic gate |

Tying liveness to data means an upstream outage restarts a healthy process,
which cannot help — so they are separate.

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
| `snapshot_records{dataset}` | gauge | rows in the current snapshot |

Alert on `batch_rejected_total` rising (upstream markup probably changed),
`refresh_failed_total` rising, and any dataset stuck at `degraded`.

Logs are structured JSON with a `request_id` on every line; secrets are
redacted by the formatter.

---

## Production deployment

```bash
docker build -t psx-service .
docker run -d --restart unless-stopped -p 8000:8000 \
  -e ENVIRONMENT=production \
  -e INTERNAL_TOKEN=your-token \
  -e CORS_ORIGINS=https://your-dashboard.example \
  -e LOG_FORMAT=json \
  --memory 256m --cpus 1 \
  psx-service
```

**Railway / Render / Fly:** point the service at this branch, let the Dockerfile
be detected, set the same environment variables. `PORT` is supplied by the
platform and honoured automatically.

Checklist before going live:

- [ ] `INTERNAL_TOKEN` set to a generated value
- [ ] `CORS_ORIGINS` set to explicit origins, not `*`
- [ ] `ENVIRONMENT=production`, `LOG_FORMAT=json`
- [ ] Readiness probe on `/ready`, liveness on `/health`
- [ ] Alerting on `batch_rejected_total` and `refresh_failed_total`

---

## Troubleshooting

**`/ready` returns 503 forever**
Check logs for `refresh_failed` or `batch_rejected`, then force a refresh:
`curl -X POST localhost:8000/internal/refresh -H "X-Internal-Token: …"`.

**`/internal/refresh` returns 401**
`INTERNAL_TOKEN` unset or mismatched. Unset means the route refuses by design
rather than sitting open.

**`batch_rejected` in the logs**
A parse returned far fewer rows than the previous good snapshot — usually
upstream markup changed. Old data keeps serving. Run `pytest tests/unit -v`.

**`ColumnMapError: upstream header changed`**
A required column vanished from the upstream table. Intentional — the parser
raises rather than guessing positions. Update `_MARKET_WATCH_SPEC` or
`_INDEX_SPEC` in `app/psx/parsers.py`.

**Indices endpoint empty**
It reads `dps.psx.com.pk/indices`, not the homepage — the homepage renders
index values client-side and yields nothing to a scraper.

**Stocks missing from `/stocks`**
`traded_only=true` is the default. Pass `traded_only=false` for all 1,020
listed instruments including those that did not trade.

---

## Contributing

```bash
pip install -r requirements-dev.txt
pytest
```

Any parser change needs a test asserting exact values against a fixture.

## License

MIT — see [LICENSE](LICENSE).

Market data belongs to the Pakistan Stock Exchange. Check their terms before
redistributing it.
