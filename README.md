# MUFAP Mutual Funds Service

Pakistan mutual fund data as a REST API: NAV, pricing, sales loads, trustee,
rating and eleven return periods. **MUFAP only** — this branch contains no PSX
code, configuration, dependencies or environment variables, and clones and runs
on its own.

| | |
|---|---|
| **Coverage** | 546 funds · 36 categories · every AMC and trustee |
| **Port** | `8001` |
| **Docs** | `/docs` (Swagger) · `/redoc` |

Sibling branches: [`pk-micro-service`](../../tree/pk-micro-service) (PSX only) ·
[`unified-service`](../../tree/unified-service) (both + dashboard).

---

## Overview

MUFAP splits fund data across two tabs of the same page, and **neither is
complete on its own**:

| Tab | Columns |
|---|---|
| `?tab=1` | Sector · Category · Fund Name · Rating · Benchmark · Validity Date · NAV · YTD · MTD · 1/15/30/90/180/270/365 Days · 2/3 Years |
| `?tab=3` | Sector · AMC · Fund · Category · Inception · Offer · Repurchase · NAV · Validity Date · Front-end · Back-end · Contingent · Market · Trustee |

So tab=1 has performance and rating but no prices or loads; tab=3 has prices,
loads and the trustee but no performance. This service fetches both and joins
them on `(sector, fund name, normalised category)` — verified as a 1:1 match
across all 546 funds.

**The join cannot use fund name alone.** 28 VPS pension funds appear two or
three times under one name, once per sub-allocation (Money Market / Debt /
Equity), and only Category distinguishes them. tab=1 suffixes its Category with
the return basis (`"VPS-Debt (Annualized Return )"`), which is stripped for the
join and surfaced separately as `return_basis`.

### Features

- Complete fund records: pricing **and** performance in one response
- Eleven return periods per fund, with the annualized/absolute basis stated
- Per-fund validity dates — funds do not all publish on the same day
- Explicit freshness — `fresh` / `stale` / `degraded` / `unavailable`
- Strict header-mapped parsers that raise on a changed column rather than guessing
- Publication gate that refuses a collapsed batch instead of overwriting good data
- Daily-publication refresh model, not fixed-interval polling

---

## Architecture

```
request path — never blocks on an upstream
  client → API layer → snapshot store → response + freshness envelope

refresh path — background scheduler
  fetch tab=1 → fetch tab=3 → merge → validate → publish

app/
├── infra/     config · http · browser_http · store · parsing · validation
│              responses · errors · security · logging · metrics
├── mufap/     parsers · service · router
├── system.py  /health · /ready · /metrics · /internal/refresh
└── main.py    application factory
```

The two tabs are fetched **sequentially, not concurrently** — two simultaneous
requests to a Cloudflare-fronted host is exactly the pattern that escalates a
soft challenge into a hard block, and the extra few seconds are irrelevant for a
once-daily figure.

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
> on Windows. `mufap-service` works.

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
uvicorn app.main:app --reload --port 8001
```

The API serves immediately at <http://localhost:8001/docs>. `/ready` returns
503 until the first refresh lands.

Force a refresh now:

```bash
curl -X POST http://localhost:8001/internal/refresh \
     -H "X-Internal-Token: YOUR_INTERNAL_TOKEN"
```

### Verify

```bash
curl -s localhost:8001/health
curl -s localhost:8001/ready
curl -s "localhost:8001/api/mufap/funds?limit=3" | python -m json.tool
curl -s localhost:8001/api/mufap/funds/categories
curl -s "localhost:8001/api/mufap/funds/top?period=ytd&limit=5"
curl -s "localhost:8001/api/mufap/funds/ABL%20Cash%20Fund"
```

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `INTERNAL_TOKEN` | *(unset)* | **Required** to enable refresh endpoints |
| `MUFAP_HTTP_CLIENT` | `curl_cffi` | `curl_cffi` or `httpx` — see [Upstream access](#upstream-access) |
| `MUFAP_IMPERSONATE` | `chrome` | TLS profile used by `curl_cffi` |
| `SCHEDULER_ENABLED` | `true` | Background refresh loop |
| `MUFAP_INTERVAL_SECONDS` | `3600` | NAV publishes once per business day |
| `MUFAP_MIN_ROWS` | `100` | Reject a batch below this |
| `MAX_ROW_DROP_RATIO` | `0.5` | Reject a batch that collapses by more than this |
| `MUFAP_STALE_AFTER_SECONDS` | `86400` | Snapshot older than this reports `stale` |
| `CORS_ORIGINS` | `*` | Comma-separated; use explicit origins in production |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per client IP; `0` disables |
| `HTTP_TIMEOUT_SECONDS` | `20` | Per upstream request |
| `HTTP_MAX_RETRIES` | `2` | Bounded; worst case 3 attempts |
| `SNAPSHOT_STORE` | `memory` | `memory` or `redis` (Upstash, for serverless) |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | |
| `PORT` | `8001` | |

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
docker build -t mufap-service .
docker run -d --name mufap -p 8001:8001 \
  -e INTERNAL_TOKEN=your-token -e ENVIRONMENT=production \
  mufap-service

docker logs -f mufap
docker exec mufap curl -s localhost:8001/health
docker stop mufap && docker rm mufap
```

Runs as non-root (`appuser`, uid 10001) with pinned wheels. The healthcheck
targets `/health` (liveness), not data availability.

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

`/funds` accepts `limit` `offset` `sort_by` `ascending` `search` `category`
`sector` `amc` `trustee` `rating` `min_nav` `max_nav` `min_ytd`.

Sortable: `fund_name` `category` `amc` `trustee` `rating` `nav` `offer_price`
`repurchase_price` `validity_date` `return_ytd` `return_mtd` `return_d365`
`return_y3`. An unknown field returns **400**, not silently unsorted data.

Return periods: `ytd` `mtd` `d1` `d15` `d30` `d90` `d180` `d270` `d365` `y2` `y3`.

```bash
BASE=http://localhost:8001
curl "$BASE/api/mufap/funds?category=Money%20Market&sort_by=return_ytd&ascending=false"
curl "$BASE/api/mufap/funds?amc=ABL&min_ytd=10"
curl "$BASE/api/mufap/funds?rating=AA&limit=20"
curl "$BASE/api/mufap/funds?trustee=CDC&sort_by=nav&ascending=false"
curl "$BASE/api/mufap/funds/top?period=y3&limit=10"
curl "$BASE/api/mufap/funds/search?q=islamic"
curl "$BASE/api/mufap/funds/stats?category=Equity"
```

### Response shape

```json
{
  "count": 1,
  "total_filtered": 546,
  "total": 546,
  "offset": 0,
  "limit": 1,
  "freshness": {
    "state": "fresh",
    "data_as_of": "2026-09-14",
    "fetched_at": "2026-09-13T21:22:51+05:00",
    "age_seconds": 0.9,
    "record_count": 546
  },
  "data": [
    {
      "fund_name": "ABL Cash Fund",
      "amc": "ABL Asset Management Company Limited",
      "category": "Money Market",
      "sector": "Open-End Funds",
      "trustee": "CDC",
      "rating": "AA+(f)",
      "benchmark": "N/A",
      "inception_date": "2010-07-31",
      "nav": 10.4862,
      "offer_price": 10.5774,
      "repurchase_price": 10.4862,
      "front_end_load": 1.0,
      "back_end_load": 2.0,
      "contingent_load": 1.0,
      "validity_date": "2026-09-14",
      "return_basis": "annualized",
      "returns": {
        "ytd": 10.63, "mtd": 10.35, "d1": 10.44, "d15": 10.37,
        "d30": 10.41, "d90": 10.87, "d180": 10.69, "d270": 10.46,
        "d365": 10.57, "y2": 12.24, "y3": 17.37
      }
    }
  ]
}
```

---

## Freshness contract

| State | Meaning |
|---|---|
| `fresh` | Fetched recently and the batch passed validation |
| `stale` | Older than the window |
| `degraded` | The last refresh failed; you are seeing the previous good data |
| `unavailable` | No usable data at all (503) |

`data_as_of` is the **newest NAV validity date** in the batch; `fetched_at` is
when we looked. Each record also carries its own `validity_date`, because funds
do not all publish on the same day — a live sample spanned Sep 09 to Sep 14.

A missing source date is never defaulted to today. Doing so is how stale data
ends up labelled current.

---

## Live-data strategy

**NAV is struck once per business day.** Polling faster cannot make the number
fresher, so the scheduler re-fetches hourly and backs off once the published
validity date advances.

| Condition | Behaviour |
|---|---|
| Validity date advanced | publish, then wait for the next interval |
| Validity date unchanged | log `mufap_unchanged`, wait |

### Upstream access

MUFAP's `robots.txt` permits this use outright:

```
User-agent: *
Content-Signal: search=yes, ai-train=no, use=reference
Allow: /
```

but the site sits behind Cloudflare bot management that rejects the request on
its **TLS fingerprint**, not on anything in the HTTP layer. Measured against
`IndustryStatDaily?tab=1`:

| Client | Result |
|---|---|
| httpx, minimal headers | 403 (challenge page) |
| httpx, full Chrome header set | 403 |
| httpx, tuned TLS cipher list | 403 |
| curl_cffi, browser TLS profile | **200** (1.27 MB) |

So `curl_cffi` is the default client. Set `MUFAP_HTTP_CLIENT=httpx` to opt out;
the endpoints will then report `degraded` or `unavailable`.

If MUFAP blocks the deployment anyway, the service keeps serving the last good
snapshot marked `degraded` and logs `mufap_challenged` — it does not go down.
The durable fix is a data agreement with MUFAP; a datacentre IP is more likely
to be challenged than a residential one.

---

## Caching

| Dataset | Retained | Bound |
|---|---|---|
| Funds | current + last known good | 2 × ~546 rows |

Two snapshots is the entire memory bound. Concurrent refreshes are
**coalesced**: a burst of requests produces one upstream fetch, not a burst of
them — which also matters for staying under Cloudflare's patience.

---

## Error handling

Every upstream call has an explicit timeout, bounded retries and exponential
backoff capped at 4s. A 403 is treated as a challenge: the client backs off
rather than hammering, since hammering turns a soft block into a hard one.

A failed refresh never takes the service down — the previous good snapshot keeps
serving marked `degraded`. With no previous snapshot, endpoints return **503**
with a reason.

A batch that parses but looks wrong is refused before publication
(`MUFAP_MIN_ROWS`, `MAX_ROW_DROP_RATIO`). Records with a missing or
non-positive NAV are dropped and counted; records whose offer/repurchase prices
sit oddly against NAV are annotated with `anomalies` and kept.

---

## Testing

```bash
pip install -r requirements-dev.txt

pytest                       # everything
pytest tests/unit -v         # parser and merge tests
pytest tests/regression -v   # one test per audit finding
pytest -k merge -v
```

Fixtures are verbatim upstream captures, so tests are deterministic and need no
network. Assertions are on **exact values** for known rows — including that
ABL Cash Fund's NAV is 10.4862 and never its 1.0 contingent load, and that
validity dates are current rather than inception dates.

Current: **30 passed, 8 skipped** (the skips are PSX cases, absent here).

---

## Health checks

| Endpoint | Purpose | Point this at |
|---|---|---|
| `/health` | Liveness | container healthcheck, restart policy |
| `/ready` | Readiness | load balancer, traffic gate |

---

## Monitoring

`/metrics` in Prometheus text format:

| Metric | Type | Meaning |
|---|---|---|
| `refresh_success_total{dataset}` | counter | successful refreshes |
| `refresh_failed_total{dataset}` | counter | failed refreshes |
| `refresh_coalesced_total{dataset}` | counter | requests merged into an in-flight fetch |
| `batch_rejected_total{dataset}` | counter | **batches refused by the publication gate** |
| `records_rejected_total{dataset}` | counter | invalid records dropped |
| `snapshot_records{dataset}` | gauge | rows in the current snapshot |

Alert on `batch_rejected_total` rising (upstream markup probably changed),
`refresh_failed_total` rising (likely a Cloudflare challenge), and a dataset
stuck at `degraded`.

Logs are structured JSON with a `request_id` on every line; secrets are
redacted by the formatter.

---

## Production deployment

```bash
docker build -t mufap-service .
docker run -d --restart unless-stopped -p 8001:8001 \
  -e ENVIRONMENT=production \
  -e INTERNAL_TOKEN=your-token \
  -e CORS_ORIGINS=https://your-dashboard.example \
  -e LOG_FORMAT=json \
  --memory 256m --cpus 1 \
  mufap-service
```

**Railway / Render / Fly:** point the service at this branch, let the Dockerfile
be detected, set the same environment variables. `PORT` is supplied by the
platform and honoured automatically.

Checklist before going live:

- [ ] `INTERNAL_TOKEN` set to a generated value
- [ ] `CORS_ORIGINS` set to explicit origins, not `*`
- [ ] `ENVIRONMENT=production`, `LOG_FORMAT=json`
- [ ] Readiness probe on `/ready`, liveness on `/health`
- [ ] Alerting on `refresh_failed_total` — a blocked IP shows up here first
- [ ] Confirm the first refresh succeeds from the deployment's IP, not just locally

---

## Troubleshooting

**All fund endpoints return 503**
The first refresh has not succeeded. Check logs for `mufap_challenged` (a
Cloudflare block) or `refresh_failed`. Force a refresh:
`curl -X POST localhost:8001/internal/refresh -H "X-Internal-Token: …"`.

**`mufap_challenged` in the logs**
Cloudflare rejected the fetch. Confirm `MUFAP_HTTP_CLIENT=curl_cffi` and that
`curl_cffi` is installed (`pip show curl_cffi`). A datacentre IP is challenged
more readily than a residential one.

**`/internal/refresh` returns 401**
`INTERNAL_TOKEN` unset or mismatched. Unset means the route refuses by design
rather than sitting open.

**A fund's returns are all null**
It appeared in tab=3 but not tab=1. The record is kept with pricing only rather
than dropped — a partial record is honest, a missing one is not.

**Three rows share one fund name**
That is correct for VPS pension funds: one record per sub-allocation
(Money Market / Debt / Equity), distinguished by `category`.

**`ColumnMapError: upstream header changed`**
A required column vanished from the upstream table. Intentional — the parser
raises rather than guessing positions. Update `_RETURNS_SPEC` or `_PRICES_SPEC`
in `app/mufap/parsers.py`.

---

## Contributing

```bash
pip install -r requirements-dev.txt
pytest
```

Any parser change needs a test asserting exact values against a fixture.

## License

MIT — see [LICENSE](LICENSE).

Fund data belongs to MUFAP. Check their terms before redistributing it, and
consider arranging a data feed for production use.
