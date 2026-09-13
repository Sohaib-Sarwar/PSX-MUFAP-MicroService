# PSX–MUFAP Microservice

Pakistan market data services. This repository holds **three independently
deployable services**, one per branch. `main` carries no service code — it
exists to point you at the right branch.

| Branch | Service | Data | Port |
|---|---|---|---|
| [`pk-micro-service`](../../tree/pk-micro-service) | PSX only | 1,020 listed instruments, 18 indices, price series | `8000` |
| [`mufap-service`](../../tree/mufap-service) | MUFAP only | 546 mutual funds: NAV, pricing, loads, 11 return periods | `8001` |
| [`unified-service`](../../tree/unified-service) | Both + dashboard | everything above, plus a React frontend | `8000` |

Each branch clones and runs on its own: its own README, Dockerfile,
dependencies, tests and environment template. The split branches contain no
code from the other domain — not merely unrouted, but absent from the tree.

---

## Which branch do I want?

- **Just equities?** `pk-micro-service`. Lighter dependencies, no Cloudflare
  handling needed.
- **Just funds?** `mufap-service`.
- **Both, or you want the dashboard, or you are deploying to Vercel?**
  `unified-service`.

```bash
git clone -b unified-service <repo-url> pk-finance
cd pk-finance
cat README.md
```

---

## What these services do

Neither upstream publishes a documented API, so both are read from public
endpoints and normalised into a stable contract.

**PSX** (`dps.psx.com.pk`) — `/symbols` gives the full listed universe with
company names, sectors and ETF/debt flags; `/market-watch` gives prices for the
instruments that traded; `/indices` gives the index board; `/timeseries/…`
gives intraday and end-of-day series as JSON. The first two are merged so
instruments that did not trade are still visible, flagged `traded: false`.

**MUFAP** (`mufap.com.pk`) — fund data is split across two tabs of one page and
neither is complete alone. `tab=1` has NAV, rating, benchmark and eleven return
periods; `tab=3` has AMC, inception, offer/repurchase prices, sales loads and
trustee. Both are fetched and joined on `(sector, fund, normalised category)`.

### Shared design

- **Freshness is explicit.** Every response carries `fresh` / `stale` /
  `degraded` / `unavailable`, with `data_as_of` (when the market last traded,
  or the NAV validity date) separate from `fetched_at` (when we looked).
- **Parsers fail loudly.** Column mapping is header-driven with no positional
  fallback. A changed upstream table raises rather than emitting plausible but
  wrong numbers.
- **Batches are gated before publication.** A result that collapses against the
  last good snapshot is refused; the previous data keeps serving, marked
  `degraded`.
- **Startup never blocks on an upstream.** The app binds immediately; `/ready`
  reports 503 until data lands. `/health` is liveness only.
- **Refresh is adaptive.** Fast while PSX trades, idle when closed; MUFAP backs
  off once its daily validity date advances.

---

## Repository layout

`main` is intentionally almost empty. Service code lives on the three service
branches, which do not share a merge base with each other by design — each is a
standalone tree.

```
main                → this README
pk-micro-service    → app/{infra,psx}       tests/  Dockerfile
mufap-service       → app/{infra,mufap}     tests/  Dockerfile
unified-service     → app/{infra,psx,mufap} tests/  Dockerfile  frontend/  api/  vercel.json
```

The `app/infra/` package is vendored identically into each branch rather than
published separately, so every branch stays independently cloneable. Keep the
copies in step when changing shared code.

---

## Local development

> Clone into a path without spaces or `&`. On Windows an `&` in the path breaks
> npm script resolution — `npm run build` fails with `MODULE_NOT_FOUND`.

```bash
git clone -b <branch> <repo-url> pk-finance && cd pk-finance
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print('INTERNAL_TOKEN=' + secrets.token_urlsafe(32))" >> .env
uvicorn app.main:app --reload
```

Then open `/docs`. Full instructions, endpoint tables and deployment guides are
in each branch's own README.

---

## License

MIT — see [LICENSE](LICENSE).

Market data belongs to the Pakistan Stock Exchange and MUFAP respectively.
Check their terms before redistributing it.
