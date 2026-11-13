# PSX & MUFAP Microservice: handoff context

Read this first, then start on the task. It is the map an engineer (or another model) needs without
re-exploring the repo. **Keep it current:** every change to this repo adds a dated line to the
[Change log](#change-log) and fixes any section it makes wrong, in the same commit.

- GitHub `Sohaib-Sarwar/PSX-MUFAP-MicroService`, **default and working branch `unified-service`**
  (cron only fires from the default branch). Other branches: `service-data` (published snapshots,
  force-pushed orphan commit), `pk-micro-service`, `mufap-service` (old).
- Consumer: the Fintraxa app (`../fintraxa-app`, its own `CONTEXT.md`). Never commit credentials;
  refer to env var names only. `docs/DEPLOYMENT.md` notes tokens once leaked into `.env.example` (rotated).
- The folder name contains `&`, which breaks npm scripts: the worker and frontend call `node` directly.

## What it does

Scrapes PSX (equities, 18 indices, session board) from `dps.psx.com.pk` and MUFAP fund NAVs/returns
(~550–570 funds) from `mufap.com.pk`, validates them, and publishes **static JSON**:

```
GitHub Actions (scripts/scrape.py)  ──► snapshot JSON ──► branch service-data (orphan, force-with-lease)
        ▲                                   │
Cloudflare Worker cron / POST /v1/refresh   └► scripts/build_static_api.py ──► ~1,463 files on GitHub Pages
                                                https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService/api/
Worker POST /v1/quotes ── live prices for ≤40 instruments (used by Fintraxa's refresh-prices)
```

FastAPI (`app/`) is the shared pipeline; it can also run as a server (Docker: memory store + scheduler;
Vercel: Upstash Redis + Vercel cron), but **production is the no-server Pages path**.

## Folder map

| Path | What |
|---|---|
| `app/main.py`, `scheduler.py`, `system.py` | app factory; adaptive refresh loop; `/health` `/ready` `/metrics` `/internal/refresh` |
| `app/infra/` | `config.py` (env + upstream URLs), `browser_http.py` (curl_cffi TLS impersonation for MUFAP), `store.py` (memory/redis/file), `validation.py` (per-record reject/flag + batch gate), `parsing.py`, `responses.py` (envelope), `errors.py`, `security.py`, `limits.py`, `metrics.py`, `logging_setup.py` |
| `app/psx/`, `app/mufap/` | `parsers.py`, `service.py`, `router.py` per domain |
| `api/index.py` | Vercel entrypoint + cron route |
| `scripts/scrape.py` | one domain's refresh as a batch job (exit 0 ok/skipped/degraded, 1 = no data) |
| `scripts/build_static_api.py` | snapshots → static tree (stdlib only); holds `SCHEDULES`, freshness/`stale_at` maths, field docs |
| `.github/scripts/` | `fetch_due.py` (is a fetch due? runs before installs), `publish-data.sh` (orphan commit + retry; each domain owns its file prefix), `watchdog.py` |
| `.github/workflows/` | see Scheduling |
| `deploy/cloudflare-worker/` | `worker.js` (crons, `/v1/refresh`, throttle, stuck-run cancel, catch-up), `quotes.js` (`/v1/quotes`, AMC registry `AMCS`), `worker.test.mjs`, `wrangler.toml` |
| `frontend/` | React 19 + Vite 6 dashboard (Landing, Overview, Stocks, Indices, Funds, ApiDocs); `lib/client.js` reads the static API |
| `static/` | built dashboard copy served by Docker |
| `tests/` | `fixtures/` (verbatim HTML, incl. AMC pages), `unit/`, `regression/test_audit_findings.py` |
| `docs/` | `ARCHITECTURE.md`, `DEPLOYMENT.md`, `MUFAP-ACCESS-REQUEST.md`, `FRONTEND-TODO.md` (schedule tables there are partly stale; YAML + `SCHEDULES` are authoritative) |

## Upstream sources

All parsers map columns **by header text** and raise `ColumnMapError` on a mismatch (no positional fallback).

- PSX `/screener` (747 rows: name, sector, index membership, price, change, market cap, P/E, dividend
  yield, free float, 30-day avg volume, 1y change); `/trading-panel` (breadth, market status);
  `/indices` (18 indices); `/company/{SYM}` (LDCP/OHLC/volume for the top ~120, `PSX_DETAIL_BUDGET`,
  stops after 5 consecutive failures). `/symbols`, `/market-watch`, `/timeseries` return 403 since 2026-09-24.
  No dividends feed (only `dividend_yield` and the `XD` flag).
- MUFAP `/Industry/IndustryStatDaily?tab=1` (NAV, rating, benchmark, 11 return periods, validity date)
  and `?tab=3` (AMC, inception, offer/repurchase, loads, trustee). Joined on (sector, fund name,
  normalised category) — VPS sub-funds share names. NAV and date from whichever tab is newer; prices
  nulled unless they belong to that date (`pick_current_nav`, `returns_as_of`, `prices_as_of`).
- AMC fallback (worker `/v1/quotes` source `amc` only): UBL and Al Meezan price pages.

## Published outputs (base `…/PSX-MUFAP-MicroService/api/`)

- Envelope: list `{count,total_filtered,total,offset,limit,freshness,data[]}`, single `{freshness,data}`.
  Freshness: `state, data_as_of, fetched_at, stale_at, computed_at, age_seconds, stale_after_seconds,
  next_refresh_at, overdue_seconds, on_schedule, record_count, schedule` (+ `error`, `market_status`).
  **Most are frozen at build time — alarm only on `stale_at` / `fetched_at`.**
- `freshness.json` (all datasets), `index.json`, `search.json`; `health.json` / `ready.json` at site root.
- PSX: `psx/stocks.json` (`symbol,name,flags,sector_code,sector,indices[],current,change,change_pct,
  change_1y_pct,market_cap,pe_ratio,dividend_yield,free_float,volume_30d_avg,has_quote,traded,ldcp/open/high/low/volume`),
  `psx/stocks/{sym}.json`, `summary`, `quoted`, `gainers|losers|active`, `top/{market-cap,dividend-yield,pe-ratio,yearly-gainers}`,
  `sectors.json`, `sectors/{slug}`, `psx/indices.json` (`index_name,high,low,current,change,change_pct`;
  `data_as_of` from the stocks session), `psx/indices/{slug}.json` (constituents, e.g. `kse100`),
  `market-status.json`, `session.json`.
- MUFAP: `mufap/funds.json` (`fund_name,sector,category,amc,trustee,rating,benchmark,inception_date,nav,
  offer_price,repurchase_price,market_price,*_load,validity_date,returns_as_of,prices_as_of,return_basis,
  returns{ytd,mtd,d1…y3},slug`; zero prices → null), `mufap/fund/{slug}.json`, `funds/{categories,amcs,
  ratings,trustees,stats}`, `category/{slug}`, `amc/{slug}`, `top|bottom/{period}`. MUFAP `data_as_of` is
  the most common `validity_date`, not the newest.
- Worker: `POST /v1/quotes` — `{source:"psx",symbols≤40}` | `{source:"mufap",tab:1|3,funds:[{name,category}]}` |
  `{source:"amc",funds:[{name,amc}]}`; edge-cached (PSX 60 s, MUFAP/AMC 10 min); a Cloudflare challenge
  returns `status:"blocked"`. `POST /v1/refresh {domain:psx|mufap|both}` → 200 / 429 (+Retry-After) / 502. `GET /v1/health`.

## Scheduling (UTC; PKT = UTC+5)

| Trigger | Schedule | Does |
|---|---|---|
| Worker cron PSX | `0 12 * * 1-5` (17:00 PKT) | dispatch PSX scrape |
| Worker cron MUFAP | `0 13-19 * * *` (18:00–00:00 PKT, **every day**) | dispatch MUFAP scrape |
| every worker fire | — | force-cancel runs `waiting` > 30 min; sweep domains past `stale_at` unless degraded. Throttle: PSX ≤ 1/240 min, MUFAP ≤ 1/20 min |
| `scrape-psx.yml` | `17 12-17 * * 1-5`, min interval 600 min, group `scrape-psx` | backstop |
| `scrape-mufap.yml` | `11,41 13-19 * * *`, min interval 45 min, group `scrape-mufap` | backstop |
| `refresh.yml` | `repository_dispatch` refresh / refresh-psx / refresh-mufap, manual | Unblock → Plan → scrape (5-min floor) → publish |
| `_scrape.yml`, `_pages.yml` | reusable | Pages deploy concurrency group **`github-pages-publish`** (renamed 2026-11-10) |
| `pages.yml` | on frontend/builder change | publish |
| `watchdog.yml` | `13,43 * * * *` | cancel stuck runs; read `freshness.json`; re-dispatch if late (not if blocked); open/close a `watchdog` issue |
| `ci.yml` | push/PR | pytest, script smoke, empty-snapshot build, frontend tests + build |

## Deploy and env

- Pages source = "GitHub Actions"; workflows need `contents: write`; only the built-in `GITHUB_TOKEN`.
- Worker secret `GITHUB_TOKEN` (fine-grained PAT: Contents RW + Actions write). `cd deploy/cloudflare-worker && npm run deploy`.
  Test AMC/MUFAP fetching with `wrangler dev --remote` — local Node gets refused (UBL TLS chain, Meezan 403).
- Server env (Docker/Vercel): `INTERNAL_TOKEN, CRON_SECRET, UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN,
  SNAPSHOT_STORE, SCHEDULER_ENABLED, SERVE_STATIC, ENVIRONMENT, CORS_ORIGINS`; tuning `PSX_*`, `MUFAP_*`,
  `MAX_ROW_DROP_RATIO`, `MUFAP_HTTP_CLIENT`, `MUFAP_IMPERSONATE(_CHAIN)`, `PSX_DETAIL_BUDGET`, `HTTP_*`, `SNAPSHOT_DIR`.
- Frontend build: `VITE_BASE, VITE_SITE_URL, VITE_REPO_URL, VITE_API_BASE, VITE_DEV_API`.

## Tests

- `pip install -r requirements-dev.txt && pytest` — ~75 tests (MUFAP parsers, PSX parsers, watchdog,
  regression) on verbatim fixtures with exact values.
- `cd frontend && npm test` (vitest); `cd deploy/cloudflare-worker && npm test` (schedules, throttle,
  catch-up, stuck runs, quotes/AMC parsing).

## Pitfalls (learned the hard way)

- GitHub cron arrives 4–6 h late or not at all; Cloudflare cron dropped whole days (2026-10-30, 2026-11-06).
  Hence dense backstops, catch-up sweep and watchdog. Check Cloudflare GraphQL `workersInvocationsAdaptive`
  for script `pk-finance-cron`.
- A Pages deploy stuck in `waiting` (2026-11-09) blocked every publish ~22 h; cancel/force-cancel didn't
  release it — renaming the concurrency group did. When data is overdue, **list Actions runs for
  `waiting`/`pending` first** before suspecting the scrapers.
- MUFAP: site-wide Cloudflare challenge since ~2026-10-28, refuses datacenter IPs since 2026-11-06 → last good
  snapshot served as `degraded` with `error`; AMC pages are the fallback. Do not try to evade the challenge.
- MUFAP posts forward-dated NAVs and on weekends; tab1/tab3 lag each other (fixed in e00b113).
- PSX: `traded` = priced in the screener; use `has_quote` for this session. `market_cap` 0 = not published.
  Use `avg_change_pct_quoted`. Split MUFAP returns by `return_basis` before aggregating.
- No holiday calendar: market status from `/trading-panel`; freshness is weekend-aware, not holiday-aware.
- The Worker's `GITHUB_TOKEN` can dispatch; cancel needs Actions: write. Unauthenticated GitHub API = 60 req/h.

## How Fintraxa uses it

- App `src/lib/api.js` → `psx/stocks.json`, `psx/stocks/{sym}.json`, gainers/losers/active/quoted/top/summary,
  `psx/indices.json`, `psx/indices/{slug}.json`, `mufap/funds.json` (all through `src/lib/marketQueries.js`).
- Supabase pg_cron + pg_net capture held closes/NAVs/indices from `psx/stocks.json`, `mufap/funds.json`,
  `psx/indices.json` daily.
- Edge Function `refresh-prices` → worker `POST /v1/quotes` (env `MARKET_QUOTES_URL`) + `mufap/funds.json`
  (`MARKET_FUNDS_URL`); `mf-import` reads `mufap/funds.json`. App `scripts/contract-test.mjs` checks the envelope.
- `../market-data/` (not a repo): one-off CSVs of 2026 PSX closes and KSE-100 closes used to backfill
  Fintraxa's price history. Not used by this service.

## Change log

Newest first. One line per change: date, commit, what and why.

- 2026-11-13 — CONTEXT.md created (handoff map for other models/accounts).
- 2026-11-10 6734de7 — Pages deploy under concurrency group `github-pages-publish` to escape a run stuck in the old group.
- 2026-11-10 f1893c1 / ea2f8ea — force-cancel stuck `waiting` runs from refresh workflow and worker.
- 2026-11-10 b9e11c9 — AMC price pages (UBL, Al Meezan) via `/v1/quotes` source `amc`; retry slow PSX pages.
- 2026-11-09 5e4df8d — `POST /v1/quotes`: latest prices for a few instruments, fetched now.
- 2026-11-08 e00b113 — newest NAV from either MUFAP tab; MUFAP sweep every evening incl. weekends.
