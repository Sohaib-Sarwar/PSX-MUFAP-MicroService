# Architecture

A read-only financial data API with no server. Two upstreams are scraped on a
schedule, validated, and published as static JSON on a CDN. Everything below
exists to make that sentence true at a particular time of day, and honest about
itself when it is not.

---

## 1. The whole system

```
    UPSTREAMS                 EXECUTION                    DELIVERY
 ─────────────────      ─────────────────────      ────────────────────────

 dps.psx.com.pk  ◀──┐
   /screener        │
   /trading-panel   │   ┌──────────────────┐
   /indices         ├───┤  GitHub Actions  │
   /company/{SYM}   │   │                  │
                    │   │  scrape.py       │
 mufap.com.pk    ◀──┘   │   fetch          │
   ?tab=1               │   parse          │
   ?tab=3               │   validate       │
                        │   gate           │
                        └────────┬─────────┘
                                 │ snapshot JSON
                                 ▼
                     ┌───────────────────────┐
                     │  branch: service-data │   one orphan commit,
                     │  psx.stocks.json      │   rewritten every run
                     │  psx.indices.json     │   (~670 KB, never grows)
                     │  psx.session.json     │
                     │  mufap.funds.json     │
                     └───────────┬───────────┘
                                 │
                        build_static_api.py
                                 │ 1,463 files
                                 ▼
                     ┌───────────────────────┐        ┌──────────────┐
                     │   GitHub Pages (CDN)  │───────▶│  consumers   │
                     │   /api/**.json        │  GET   │  dashboard   │
                     │   dashboard (SPA)     │        │  other apps  │
                     └───────────────────────┘        └──────────────┘
```

**Why static.** Every read endpoint of the original FastAPI service is a pure
function of one snapshot, so each one is precomputed at publish time. What a
query parameter selected on the live API is selected here by path:

```
GET /api/psx/stocks/gainers?limit=50   →   /api/psx/stocks/gainers.json
```

Response bodies keep the same envelope, so a consumer written against either
works against the other. The live service still exists for anyone who wants
server-side filtering; see the README.

---

## 2. What triggers a run

This is the part that took three attempts to get right, because the obvious
answer does not work.

```
  ┌─────────────────────────┐
  │  Cloudflare cron        │  fires within seconds
  │  0 12    * * 1-5        │───────────────┐         ◀── THE PUNCTUAL PATH
  │  0 13-19 * * 1-5        │               │
  └─────────────────────────┘               │
                                            ▼
  ┌─────────────────────────┐    POST /repos/…/dispatches
  │  Dashboard button       │    (worker holds the token)
  │  POST /v1/refresh       │───────────────┤         ◀── ON DEMAND
  └─────────────────────────┘               │
                                            │
  ┌─────────────────────────┐               │
  │  Any app                │───────────────┤
  │  POST /v1/refresh       │               │
  └─────────────────────────┘               ▼
                                   ┌──────────────────┐
  ┌─────────────────────────┐      │  refresh.yml     │
  │  GitHub cron            │      │  5-min floor     │
  │  0    12-17 * * 1-5     │      └──────────────────┘
  │  0,30 13-19 * * 1-5     │──┐
  └─────────────────────────┘  │   ┌──────────────────┐
         ◀── THE BACKSTOP      └──▶│  scrape-*.yml    │
                                   │  due? ─ no ─▶exit│
                                   │       └─ yes ─┐  │
                                   └───────────────┼──┘
                                                   ▼
                                            fetch and publish
```

### Why GitHub's cron is a backstop and not the schedule

Measured on this repository over five consecutive working days, against crons
asking for 12:00 UTC and seven hourly fires 13:00–19:00 UTC:

| Workflow | Asked for | GitHub delivered |
|---|---|---|
| PSX | 12:00 UTC | 16:17, 17:03, 16:49, 16:50, 18:11 — **4–6 h late, daily** |
| MUFAP | 7 fires/day | **1–2 a day**; the rest were never created |

Nothing failed and nothing was cancelled. `schedule` is best effort and drops
runs under load. That cannot be fixed inside the repository, so:

1. **Cloudflare cron** calls `repository_dispatch`, which starts a run within
   seconds. That is where the published times come from.
2. **GitHub cron stays**, asking for far more fires than needed, so that
   whatever fraction is honoured still lands near the intended times.
3. **A fire that is not needed costs seconds.** `fetch_due.py` decides before
   `setup-python` and `pip`, using the runner's preinstalled Python and the
   snapshot just checked out. Fourteen fires cost fourteen short jobs, not
   fourteen scrapes of someone else's website.

The due check keys off **when a fetch last succeeded**, never off whether the
data *looks* current. Those differ: a MUFAP correction issued at 22:00 to a NAV
struck at 18:00 leaves the data looking perfectly current while being wrong.

---

## 3. Trust boundaries

```
  ┌──────────────────────────── PUBLIC ────────────────────────────┐
  │                                                                 │
  │   Browser / any app                                             │
  │     • holds nothing                                             │
  │     • POST /v1/refresh          (no credential)                 │
  │     • GET  api.github.com/…     (public repo, read only)        │
  │     • GET  …github.io/api/…     (static JSON)                   │
  │                                                                 │
  └────────────────────────────┬────────────────────────────────────┘
                               │
  ┌────────────────────────────▼──────── CLOUDFLARE ────────────────┐
  │   Worker                                                        │
  │     • GITHUB_TOKEN  ── secret, never in a response, never       │
  │                        logged, never sent anywhere but          │
  │                        api.github.com                           │
  │     • throttles from the published freshness file               │
  └────────────────────────────┬────────────────────────────────────┘
                               │ repository_dispatch
  ┌────────────────────────────▼──────── GITHUB ────────────────────┐
  │   Actions runner                                                │
  │     • GITHUB_TOKEN  ── scoped per job: contents:write only on   │
  │                        the scrape job, pages:write only on      │
  │                        deploy, read elsewhere                   │
  └─────────────────────────────────────────────────────────────────┘
```

### Why the refresh endpoint has no key

A static site cannot keep a secret — anything shipped to the browser is public
by definition, so a key there would be theatre with a login prompt attached.
The endpoint is open and protects itself by **refusing to do anything
pointless**: it reads the published freshness file and declines if the data was
fetched more recently than it could possibly have changed.

| Source | Minimum interval | Why |
|---|---|---|
| PSX | 240 min | One closing board per trading day. A refresh four hours later cannot return different numbers. |
| MUFAP | 20 min | NAV lands at an unpredictable evening hour, so twenty minutes is the shortest interval that can carry news. |

Overriding the throttle is deliberately impossible here — that means running
the workflow from the Actions tab, where GitHub has already authenticated you.

The throttle needs no KV namespace, no Durable Object and no state in the
worker, because it is derived from data that is already published. That also
makes it **eventually consistent**, and the gap is real: requests arriving in
the two minutes it takes a run to publish all read the same freshness file and
all pass. Measured — three requests one second apart all dispatched.

So the bound is layered, and each layer catches what the one above cannot:

| Layer | Catches | Observed |
|---|---|---|
| Worker throttle | the steady state | 6 PSX / 72 MUFAP dispatches a day |
| GitHub concurrency group | the burst | 1 running + 1 queued; the rest **cancelled** |
| `min_interval_minutes: 5` on the dispatch path | the queued one | it starts after the first published, sees data 2 min old, exits in seconds |

A burst therefore costs one scrape, not one per request. No single layer is
sufficient and none of them is load-bearing alone.

---

## 4. The pipeline, per run

```
  fetch ──▶ parse ──▶ validate ──▶ gate ──▶ publish ──▶ build ──▶ deploy
    │         │          │          │         │           │         │
    │         │          │          │         │           │         └─ Pages
    │         │          │          │         │           └─ 1,463 JSON files
    │         │          │          │         └─ orphan commit, force-with-lease
    │         │          │          └─ reject the batch if rows collapsed
    │         │          └─ reject impossible rows; flag odd-but-real ones
    │         └─ header-driven column maps, no positional fallback
    └─ bounded retries, explicit timeouts, browser TLS profile for MUFAP
```

**Parsing is strict.** Column maps are built from headers, and there is
deliberately no positional fallback: the previous implementation had one, and
when it fired it shifted every numeric field by one column — reporting a
stock's volume as `4` instead of `104,274,125`. A positional guess produces
plausible, wrong financial data. Raising `ColumnMapError` keeps the last good
snapshot serving and makes a parser regression visible.

**Validation has two levels**, kept separate on purpose:

- **Reject** — impossible data. A negative price, a low above a high. Parser bugs.
- **Flag** — unusual but real. An illiquid instrument whose last price sits
  outside today's range, because `current` falls back to the last trade while
  high/low cover only today's. Dropping these would discard legitimate quotes
  for thinly traded stocks, which is its own correctness bug.

**The gate** refuses a batch whose row count collapsed against the previous
snapshot. This is what stops a parser regression replacing 500 instruments
with 3.

**Publishing is atomic and bounded.** `service-data` is rewritten as a single
orphan commit, so the branch costs what the current snapshot costs — about
670 KB — however many times a day it is replaced. PSX and MUFAP hold separate
concurrency groups and can genuinely overlap, so pushes use
`--force-with-lease` and retry: on rejection the branch is re-read, the other
domain's files re-taken, and the push retried.

---

## 5. Freshness, and being honest about lag

Every response carries an envelope. It is the contract that matters most,
because a dashboard that cannot distinguish live prices from Friday's close
re-read on a Sunday is a dashboard that quietly lies twice a week.

```json
{
  "state": "fresh",
  "data_as_of": "2026-09-25",
  "fetched_at": "2026-09-27T01:55:39+05:00",
  "age_seconds": 74,
  "stale_after_seconds": 7200,
  "next_refresh_at": "2026-09-28T18:00:00+05:00",
  "overdue_seconds": 0,
  "on_schedule": true,
  "record_count": 553
}
```

| Field | Meaning |
|---|---|
| `data_as_of` | What the **source** dates it — NAV validity date, or the session stamp PSX printed on the board. Not the same as `fetched_at`, and the one that identifies the session. |
| `fetched_at` | When this service pulled it. |
| `age_seconds` | Measured **when the file was written**. A static file cannot tick — recompute from `fetched_at`. |
| `stale_after_seconds` | Derived from the schedule, so it already accounts for weekends. |
| `on_schedule` / `overdue_seconds` | Alarm on these. `false` means a refresh that should have happened has not. |

Weekend-aware by construction: a Friday snapshot read on Sunday is
`on_schedule: true`, because Monday's run genuinely is not due yet.

---

## 6. Failure modes

Each of these has happened or was designed against, and each degrades rather
than breaking.

| Failure | Behaviour |
|---|---|
| Upstream returns nothing | Last good snapshot kept, marked `degraded`, error surfaced in the envelope. Site stays up. |
| Parser sees changed markup | `ColumnMapError`, batch refused, previous snapshot serves. Visible, not silent. |
| Row count collapses | Publication gate rejects the batch. |
| PSX withdrew `/symbols`, `/market-watch`, `/timeseries` (2026-09-24) | Rebuilt on `/screener`, `/trading-panel`, `/indices`. `has_quote` marks which rows still carry OHLC rather than filling nulls that would read as "did not trade". |
| MUFAP's Cloudflare refuses the runner | Browser TLS profile, session reuse, homepage warm-up, four profiles tried in turn. If still refused: `degraded`, last good data served. |
| PSX refuses the per-instrument quote pass | Five consecutive failures abandon the pass. Snapshot publishes with `has_quote: false`. |
| GitHub cron drops fires | Cloudflare dispatch is the primary; dense backstop covers the rest. |
| Both domains publish at once | `--force-with-lease` + retry. Verified against a simulated race. |
| Data branch lost | Cold start: every script handles an empty snapshot set; CI exercises it. |
| Refresh endpoint abused | Throttled from published data. Bounded at 6 + 72 runs/day. |

---

## 7. Repository map

```
app/                    the pipeline, shared by every deployment
  infra/                config · http · browser_http · store · parsing
                        validation · responses · errors · limits · metrics
  psx/      parsers · service · router
  mufap/    parsers · service · router
scripts/
  scrape.py             one domain's refresh as a batch job
  build_static_api.py   snapshots → 1,463 JSON files (stdlib only)
.github/
  workflows/            scrape-psx · scrape-mufap · refresh · pages · ci
  scripts/              fetch_due.py · publish-data.sh
deploy/cloudflare-worker/
  worker.js             cron + refresh API; holds the GitHub token
frontend/               React dashboard, reads the same public API
docs/                   this file · DEPLOYMENT.md
tests/                  57 python · fixtures are real captured markup
```

**Test coverage of the moving parts:**

| Suite | Count | Covers |
|---|---|---|
| `tests/` (pytest) | 57 | parsers against real markup, validation, envelopes, regressions |
| `frontend` (vitest) | 17 | refresh flow, progress, abort, cache-busting, no-credential guard |
| `deploy/cloudflare-worker` (node) | 26 | schedule mapping, throttle, worst-case load |

---

## 8. Design decisions worth knowing

**Static over dynamic.** No server to keep alive, no database to pay for, a CDN
in front of everything, and a consumer's request never reaches PSX or MUFAP.

**One orphan commit.** History of a dataset that is replaced daily is a few
hundred megabytes a year that nobody will ever read.

**Rate limit the data, not the caller.** The harm is a redundant scrape of
someone else's website, and that is the same harm however many callers cause
it. This also removes all state from the worker.

**Never fill a field to avoid a null.** `has_quote: false` says "this was not
fetched". Filling `volume: null` alongside `traded: true` would say "it did not
trade", which is a different and wrong claim.

**Say when you are late.** `on_schedule` exists so lag is a field a consumer
can alarm on, rather than something they discover.
