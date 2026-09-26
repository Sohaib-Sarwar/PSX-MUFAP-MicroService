# Punctual trigger

GitHub's `schedule` event is best effort. Measured on this repository over five
consecutive working days:

| Workflow | Asked for | GitHub delivered |
|---|---|---|
| PSX | 12:00 UTC | 16:17, 17:03, 16:49, 16:50, 18:11 — **4–6 hours late, daily** |
| MUFAP | 7 fires, 13:00–19:00 UTC | **1–2 a day**; the rest were never created |

Nothing failed and nothing was cancelled — the runs were simply never made.
That is documented GitHub behaviour and cannot be fixed inside the repository.

`repository_dispatch`, by contrast, starts a run within seconds of the POST.
This worker calls it on Cloudflare's cron triggers, which are punctual. It is
what makes the times published in the README true rather than aspirational.

## Deploy

This folder is a complete, ready-to-deploy worker — there is no scaffolding
step and nothing interactive except the Cloudflare login itself.

```bash
cd deploy/cloudflare-worker
npm install
npm run login       # opens your browser once, to authorise Cloudflare
npm run secrets     # prompts for GITHUB_TOKEN — the only secret
npm run deploy
```

> **Use `npm run login`, not `npx wrangler login`.** This repository's path
> contains an `&` (`PSX & MUFAP Microservice`). `npx` and npm's own bin shims
> resolve the tool to an absolute path and hand it to `cmd.exe`, which treats
> `&` as a command separator and truncates it — you get
> `'MUFAP' is not recognized` and a `MODULE_NOT_FOUND` for a path ending
> `Fintraxa\wranglerin\wrangler.js`. The scripts here invoke `node` against
> a *relative* path, so no shell ever parses the `&`. The frontend's scripts
> do the same thing for the same reason.

Check the plumbing before authorising anything:

```bash
npm run whoami      # "You are not authenticated" is the correct answer here
```

**The token** is a fine-grained personal access token scoped to this one
repository with a single permission — *Repository permissions → Contents: Read
and write*. That is the least GitHub accepts for `repository_dispatch`. It
cannot read your other repositories and it cannot act on your account.

Free tier covers this comfortably: 12 invocations a working day, each a single
outbound request.

## Check it works

```bash
curl "https://pk-finance-cron.<your-subdomain>.workers.dev/?key=YOUR_TRIGGER_SECRET&domain=mufap"
# {"dispatched":"mufap","at":"..."}
```

On PowerShell, quote the whole URL — an unquoted `&` splits the command there
too:

```powershell
curl.exe "https://pk-finance-cron.<your-subdomain>.workers.dev/?key=YOUR_TRIGGER_SECRET&domain=mufap"
```

Then watch **Actions → Refresh on demand** in the repository.

## The one credential

`GITHUB_TOKEN` — a fine-grained PAT scoped to this repository with a single
permission, *Contents: Read and write*, the least GitHub accepts for
`repository_dispatch`. It lives as a Cloudflare secret, is never sent anywhere
but `api.github.com`, never appears in a response body, and is never logged.

`wrangler secret put` is prompt-only for exactly this reason: the value is not
echoed and never touches disk. Never put it in this repository, in a committed
`.env`, in a frontend build, or in a chat window.

Rotate it by generating a new PAT and running `npm run secrets` again.

## The refresh API — no key

```
POST https://pk-finance-cron.pk-microservice.workers.dev/v1/refresh
Content-Type: application/json

{"domain": "mufap"}
```

`domain` is `psx`, `mufap` or `both`.

| Status | Meaning |
|---|---|
| `200` | Dispatched. Body names what ran and how to follow it. |
| `400` | Unknown domain. |
| `429` | Refreshed too recently to have changed. `Retry-After` says how long. |
| `502` | GitHub refused — usually an expired `GITHUB_TOKEN`. |

```bash
curl -X POST https://pk-finance-cron.pk-microservice.workers.dev/v1/refresh   -H "Content-Type: application/json" -d '{"domain":"mufap"}'
```

`GET /` describes the service and `GET /v1/health` is liveness; neither needs
anything either.

### Why it is open, and why that is safe

A static dashboard cannot hold a secret — anything shipped to the browser is
public by definition, so a key there would be theatre with a login prompt
attached. The endpoint is open and protects itself by refusing to do anything
pointless: it reads the published freshness file and declines if the data was
fetched more recently than the source could possibly have changed it.

| Source | Minimum interval | Why |
|---|---|---|
| PSX | 240 min | One closing board per trading day. Four hours later cannot differ. |
| MUFAP | 20 min | NAV posts at an unpredictable evening hour. |

The limit is derived from data that is already published, so it needs no KV
namespace and no state in the worker — and that makes it eventually consistent.
Requests arriving in the two minutes a run takes to publish all read the same
file and all pass; three requests a second apart were measured doing exactly
that.

The bound is therefore layered: the throttle holds the steady state at 6 PSX
and 72 MUFAP dispatches a day, GitHub's concurrency group cancels all but one
queued run from a burst, and the dispatch workflow keeps a five-minute floor so
the one that does start exits in seconds having found the data already fresh.
A burst costs one scrape, not one per request.

Overriding the throttle is deliberately not possible here — that means running
the workflow from the Actions tab, where GitHub has already authenticated you.

## Without Cloudflare

Any scheduler that can POST will do — [cron-job.org](https://cron-job.org) is
free and needs no code:

```
URL     https://api.github.com/repos/Sohaib-Sarwar/PSX-MUFAP-MicroService/dispatches
Method  POST
Headers Accept: application/vnd.github+json
        Authorization: Bearer <token>
Body    {"event_type":"refresh","client_payload":{"domain":"mufap"}}
```

The repository's own cron keeps running either way, as the backstop. If the
external trigger stops, the data keeps updating — just less punctually, which
is the situation this worker exists to improve on.
