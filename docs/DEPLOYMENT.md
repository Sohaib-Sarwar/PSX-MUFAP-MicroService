# Deploying to GitHub Pages and GitHub Actions

This deployment has no server. GitHub Actions runs the scrapers on a schedule
and GitHub Pages serves the result — the dashboard and the whole read API, as
static files on a CDN.

Nothing about the data pipeline changes. The scheduled job imports the same
`app.psx.service` and `app.mufap.service` the HTTP deployment does, so the
parsers, the per-record validation, the batch publication gate and the freshness
envelope are one implementation, exercised two ways.

---

## How it fits together

```
  ┌─ 17:10 PKT, Mon–Fri ─────────┐   ┌─ 18:05–00:05 PKT, Mon–Fri ───┐
  │  scrape-psx.yml              │   │  scrape-mufap.yml            │
  │    dps.psx.com.pk            │   │    mufap.com.pk              │
  └───────────┬──────────────────┘   └──────────────┬───────────────┘
              │      scripts/scrape.py              │
              │      fetch → validate → gate        │
              └──────────────┬──────────────────────┘
                             ▼
                 branch: service-data   (one orphan commit, rewritten each run)
                   data/psx.stocks.json
                   data/psx.indices.json
                   data/mufap.funds.json
                             │
                             ▼   _pages.yml
             scripts/build_static_api.py  +  vite build
                             │
                             ▼
              GitHub Pages — dashboard + /api/**.json
```

Two workflows write to one branch, so both declare the same
`concurrency: market-data` group and only one ever runs at a time. Each owns
only the files whose names start with its own prefix; `publish-data.sh` restores
the other domain's files from the branch tip before committing, so a PSX run
cannot roll back fund data published an hour earlier.

The `service-data` branch is force-pushed as a **single orphan commit** every
time. A working day produces roughly a megabyte of superseded JSON; keeping that
history would add a few hundred megabytes a year to a branch nobody checks out,
and every clone — including the next run's — would pay for it.

---

## One-time setup

### 1. Push the branch

```bash
git push -u origin unified-service
```

### 2. Make `unified-service` the default branch — required

**Settings → General → Default branch → switch to `unified-service`.**

This is not cosmetic. GitHub runs `schedule` triggers **only from the workflow
files on the repository's default branch**. If the default stays `main` while the
workflows live on `unified-service`, no cron will ever fire and no error will be
reported — the schedules simply do not exist as far as GitHub is concerned.

If you would rather keep `main` as the default, merge `unified-service` into it
instead; everything here works unchanged from `main`.

### 3. Turn on Pages

**Settings → Pages → Build and deployment → Source: _GitHub Actions_.**

Not "Deploy from a branch" — that path runs Jekyll over the repository and
ignores the artifact these workflows build.

### 4. Seed the data

The site publishes fine with no data (it renders an explicit empty state), but
there is no reason to wait for the next scheduled run:

**Actions → "PSX — daily close" → Run workflow**, then the same for
**"MUFAP — evening NAV"**. Each finishes in well under two minutes and publishes
the site when it is done.

The site then lives at `https://<owner>.github.io/<repo>/` and the API at
`https://<owner>.github.io/<repo>/api/`.

### 5. If the first run fails to push

**Settings → Actions → General → Workflow permissions → Read and write
permissions.** The workflows request `contents: write` explicitly for the one
job that needs it, but an account or organisation policy can cap what a workflow
is allowed to ask for.

---

## The schedule

Pakistan Standard Time is UTC+5 year-round — Pakistan abolished DST in 2009 —
so the mapping is a fixed five-hour shift with no seasonal correction.

| Workflow | PKT | UTC cron | Runs per week |
| --- | --- | --- | --- |
| `scrape-psx.yml` | 17:10, Mon–Fri | `10 12 * * 1-5` | 5 |
| `scrape-mufap.yml` | 18:05 → 00:05 hourly, Mon–Fri | `5 13-19 * * 1-5` | up to 35, typically 5–10 |
| `pages.yml` | on change only | — | — |
| `ci.yml` | on push and PR | — | — |

**Why these times.** PSX publishes one closing board per trading day; 17:10 PKT
is about half an hour after the close. MUFAP strikes NAV once per business day
but publishes it at no fixed hour, so the evening is swept rather than guessed
at. Neither upstream changes between runs, which is the whole argument for the
cadence: the previous deployment polled every 30 minutes around the clock and
roughly four fetches in five returned data that had not moved.

**The MUFAP sweep stops itself.** Each run reads the published snapshot first,
and if the NAV validity date already covers the current session it exits without
opening a connection to mufap.com.pk. A normal evening therefore costs one or two
fetches out of a possible seven. The session date is computed with a six-hour
shift so the run that fires at midnight is attributed to the session that just
ended rather than to the day that just began.

**Minutes 10 and 05, not 00.** GitHub's scheduler is busiest at the top of the
hour and queues cron runs the longest there.

**Scheduled workflows drift, and they expire.** A run can land several minutes
late under load, and GitHub disables scheduled workflows in a public repository
after **60 days with no activity**. Any commit re-enables them. Consumers should
read `freshness.fetched_at` rather than assume the clock.

---

## What each workflow does

| File | Trigger | Job |
| --- | --- | --- |
| `scrape-psx.yml` | cron, manual | Calls `_scrape.yml` for PSX, then `_pages.yml` |
| `scrape-mufap.yml` | cron, manual | Calls `_scrape.yml` for MUFAP, then `_pages.yml` |
| `_scrape.yml` | reusable | Restore snapshots → fetch → validate → publish to `service-data` |
| `_pages.yml` | reusable | Build the static API and the dashboard, deploy to Pages |
| `pages.yml` | push to `frontend/**`, manual | Republish the site without scraping |
| `ci.yml` | push, PR | Tests, plus the cold-start and base-path checks |

A scrape reports one of four outcomes, and the publish job is skipped for two of
them:

| Status | Meaning | Exit | Publishes? |
| --- | --- | --- | --- |
| `ok` | Fresh snapshot accepted | 0 | yes |
| `degraded` | Fetch failed; last good snapshot retained and labelled | 0, with a warning annotation | yes |
| `skipped` | Published data already covers this session | 0 | no |
| `failed` | No usable data at all | 1 | no |

`degraded` is deliberately not a job failure. The site is serving correct data
that is clearly labelled as degraded, and a red run for that is noise rather than
signal — the annotation and the on-page badge both say what happened.

---

## Resource use

Each run is deliberately small:

- **`requirements-scrape.txt`, not `requirements.txt`.** The batch job never
  imports a web framework, so FastAPI, Starlette and uvicorn are not installed —
  five packages instead of nine.
- **A hard memory ceiling.** `MAX_MEMORY_MB=1024` applies `RLIMIT_AS` to the
  process, so a runaway allocation raises a `MemoryError` naming the limit
  instead of being OOM-killed as a bare exit 137. Measured peak for a full run
  is well under 200 MiB, and the ceiling sits well above that on purpose:
  `RLIMIT_AS` caps *address space*, and glibc reserves a 64 MiB arena per thread.
  `MALLOC_ARENA_MAX=2` caps that at two arenas, which both lowers the real
  footprint and keeps the reservation far from the limit. The run reports its
  peak RSS in the job summary, so the ceiling can be tuned from measurement.
- **Datasets are released between stages.** PSX fetches stocks and indices in
  sequence and drops the first before starting the second, so the two are never
  both resident.
- **The deploy job installs no Python packages at all.**
  `scripts/build_static_api.py` is standard library only.
- **`timeout-minutes` on every job**, and pip and npm caches keyed to their
  lockfiles.

Typical cost: about 8 runs on a working day, each finishing in one to two
minutes. Public repositories are not billed for Actions minutes at all.

---

## Staying inside GitHub's policies

The [Actions usage policy](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)
requires that Actions be used for the software project it belongs to and not as
free general-purpose compute. This deployment builds, tests and publishes this
repository's own site and dataset, which is squarely what Actions is for. The
concrete commitments:

- **Modest, bounded schedules.** Around 8 short runs on a working day, none at
  the top of the hour, nothing at weekends.
- **`concurrency` guards on everything**, so a slow run queues rather than
  piling up alongside its successor.
- **`timeout-minutes` on every job.** No job can hold a runner indefinitely.
- **Least-privilege tokens.** Only the scrape job gets `contents: write`; only
  the deploy job gets `pages: write` and `id-token: write`; everything else is
  read-only.
- **No forbidden workloads** — no cryptocurrency mining, no serverless compute
  for third parties, no relay or proxying.
- **Polite upstream access.** Both sites' `robots.txt` permit these requests.
  The client identifies itself with a descriptive `User-Agent` that links back to
  this repository, retries are bounded with exponential backoff, MUFAP's two tabs
  are fetched sequentially rather than in parallel, and the whole point of the
  schedule is to ask for each figure once.

---

## Local development

```bash
# 1. Scrape once into ./data
pip install -r requirements-scrape.txt
python scripts/scrape.py --domain psx
python scripts/scrape.py --domain mufap

# 2. Build the static API where the dev server will serve it
cd frontend && npm install && npm run data

# 3. Run the dashboard against exactly what Pages will serve
npm run dev
```

`npm run data` writes into `frontend/public/api/`, which is git-ignored — the dev
server then reads the identical files the deployed site reads, so what you see
locally is what ships.

To develop against the live FastAPI service instead, set `VITE_API_BASE`:

```bash
uvicorn app.main:app --reload            # terminal 1
VITE_API_BASE=http://127.0.0.1:8000 npm run dev   # terminal 2
```

To build the whole site exactly as the workflow does:

```bash
cd frontend && VITE_BASE=/PSX-MUFAP-MicroService/ npm run build && cd ..
python scripts/build_static_api.py --data data --out frontend/dist/api --site-out frontend/dist
cp frontend/dist/index.html frontend/dist/404.html
```

> **On Windows, do not run that first line in Git Bash.** MSYS rewrites any
> argument that looks like a Unix path, so `VITE_BASE=/PSX-MUFAP-MicroService/`
> arrives as `C:/Program Files/Git/PSX-MUFAP-MicroService/` and every asset URL
> in the built `index.html` is wrong. Use PowerShell
> (`$env:VITE_BASE = '/PSX-MUFAP-MicroService/'; npm run build`) or prefix the
> command with `MSYS_NO_PATHCONV=1`. The Actions runners are Linux and are not
> affected.

---

## Troubleshooting

**Nothing runs on schedule.** The workflow files are not on the default branch.
See step 2 — this is the single most common cause.

**Schedules stopped after a couple of months.** A public repository with 60 days
of no activity has its scheduled workflows disabled. Push any commit, or
re-enable them from the Actions tab.

**`remote: Permission to ... denied` in the publish step.** Workflow permissions
are capped below `contents: write`. See step 5.

**The deploy job fails with a Pages error.** Pages source is still set to "Deploy
from a branch". See step 3.

**The site loads but every panel says "This dataset has not been published
yet".** The `service-data` branch has no snapshots. Run either scrape workflow
manually.

**MUFAP runs are refused by Cloudflare — the known hard case.**

This one is real and was hit on the very first scheduled run. The same
`curl_cffi` request that succeeds from a Pakistani residential connection was
refused three times from a GitHub-hosted runner. Cloudflare scores TLS
fingerprint *and* source-IP reputation, and GitHub's Azure ranges start from a
much worse prior than any home connection.

What the client now does about it (`app/infra/browser_http.py`):

- **One session per run**, so the `__cf_bm` cookie a successful request earns is
  still there for the next one. Previously every request opened a new connection
  and threw that away.
- **A warm-up request to the site root** before any data page, which is how a
  visitor actually arrives — and how the clearance cookie is issued.
- **A `Referer`** consistent with having done so.
- **Four TLS profiles tried in turn** — Chrome, Safari and Firefox builds — with
  escalating, jittered backoff measured in tens of seconds. A challenge is the
  one failure that retrying quickly makes strictly worse.
- **Diagnostics worth reading**: the run logs the status, body size, `cf-ray`
  and `cf-mitigated` for each refusal, so the next failure says which layer
  refused rather than just that something did.

Tune it without editing code:

```
MUFAP_IMPERSONATE_CHAIN=safari184,chrome146,firefox144   # try these, in order
MUFAP_RETRY_BACKOFF_SECONDS=20                           # start of the backoff ramp
```

**If it is still refused**, the source address is the part that cannot be
argued with from inside a GitHub runner. In rough order of effort:

1. **Seed the branch once from a machine that is not refused.** Scrape locally
   and publish the snapshot by hand:

   ```bash
   python scripts/scrape.py --domain mufap
   DATA_BRANCH=service-data .github/scripts/publish-data.sh 'mufap.'
   ```

   This is worth doing regardless. With *any* previous snapshot present, a
   blocked run becomes `degraded` (exit 0, a warning, last-good data still
   served) instead of `failed` (exit 1, a red run and an email). The cold-start
   case is the only one that fails outright, and seeding removes it.

2. **Run the MUFAP job on a self-hosted runner** with a residential address —
   a spare machine or a small VPS. Change one line in `scrape-mufap.yml`:

   ```yaml
   # in .github/workflows/_scrape.yml, or override per-domain
   runs-on: self-hosted
   ```

   PSX is unaffected and stays on GitHub's runners.

3. **Ask MUFAP.** Their `robots.txt` says `Allow: /` with
   `Content-Signal: use=reference`, so the access is permitted and the block is a
   heuristic misfire. A note to their IT with a `cf-ray` from the job log is the
   clean fix, and the logs now carry one.

**A MUFAP run says `curl_cffi_unavailable`.** The package did not install; the
client fell back to plain httpx, which MUFAP answers with 403 every time. Check
the install step in the job log.

**Assets 404 after a rename.** The base path is derived from the repository name
at build time. Re-run `pages.yml` after renaming the repository.

---

## Rotating the credentials that were in `.env.example`

`.env.example` previously carried what look like a real Upstash REST URL and
token and a real `INTERNAL_TOKEN`. They are now placeholders, but **replacing
them in the working tree does not remove them from git history** — anyone who has
ever cloned the repository still has them.

Rotate them at the source:

1. Upstash console → the database → **Rotate token** (or delete and recreate it).
2. Generate a fresh internal token and set it wherever the service reads it:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`
3. Update the Vercel environment variables if that deployment is still live.

None of these values are needed by the GitHub Pages deployment — it has no
database and no authenticated endpoint.
