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
npm run secrets     # prompts for GITHUB_TOKEN, then TRIGGER_SECRET
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

## Where credentials live, and why

One rule: **the GitHub token never leaves the worker.**

Triggering a workflow needs a credential that can write to the repository.
Handing that to every caller — a browser, an external service — hands every
caller the ability to rewrite the repository, to do a job whose entire scope is
"scrape now". So callers present a **refresh key** instead: a credential whose
only capability is asking for a scrape, issued per consumer, revocable one at a
time. A leaked refresh key costs an unnecessary scrape. A leaked GitHub PAT is
an incident.

| Credential | Lives in | Who holds it | If it leaks |
|---|---|---|---|
| `GITHUB_TOKEN` | Cloudflare secret | nobody — the worker only | Repository write. Revoke at GitHub immediately. |
| Refresh key | Cloudflare secret (`API_KEYS`), and the consumer's own config | each consumer | One extra scrape, rate-limited. Delete that one key. |

Never put either in the repository, in a `.env` that is committed, in a chat
window, or in a frontend build. `wrangler secret put` is prompt-only for this
reason — the value is not echoed and not stored on disk.

### Issuing keys

`API_KEYS` is a JSON object of consumer name to key. Generate real random keys:

```bash
# one key per consumer, so any one of them can be revoked alone
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```bash
npm run keys    # prompts for API_KEYS; paste the whole JSON object
```

```json
{
  "dashboard": "PASTE_A_GENERATED_KEY",
  "partner-app": "PASTE_ANOTHER_GENERATED_KEY"
}
```

To revoke one consumer, remove its entry and run `npm run keys` again. Nothing
else changes, and no other consumer is disturbed.

### Storing a key as a consumer

- **A server or scheduled job** — your platform's secret store: GitHub Actions
  secrets, Vercel/Netlify environment variables, Docker secrets, AWS Secrets
  Manager. Never a committed file.
- **The dashboard** — entered once, kept in that browser's local storage, so it
  is not asked for again. It is only a refresh key, but anyone with access to
  that browser profile has it; give the dashboard its own key so revoking it
  costs nothing else. For a dashboard exposed to people you do not control, put
  [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
  in front of the worker instead and drop the key entirely — free for up to 50
  users, and then the browser holds no credential at all.

## The refresh API

```
POST https://pk-finance-cron.pk-microservice.workers.dev/v1/refresh
Authorization: Bearer <refresh key>
Content-Type: application/json

{"domain": "mufap", "force": false}
```

`domain` is `psx`, `mufap` or `both`. Responses:

| Status | Meaning |
|---|---|
| `200` | Dispatched. Body names what actually ran and how to follow it. |
| `401` | Key not recognised. |
| `429` | That domain was scraped within its minimum interval. `Retry-After` and `retry_after_seconds` say how long; `{"force": true}` overrides. |
| `502` | GitHub refused the dispatch — usually an expired `GITHUB_TOKEN`. |
| `503` | No keys configured. |

```bash
curl -X POST https://pk-finance-cron.pk-microservice.workers.dev/v1/refresh   -H "Authorization: Bearer $PK_REFRESH_KEY"   -H "Content-Type: application/json"   -d '{"domain":"mufap"}'
```

Other endpoints need no credential: `GET /` describes the service, and
`GET /v1/health` is liveness.

**Following a run needs no credential either.** The repository is public, so the
response's `track` block points at the run list and the freshness file, both
readable anonymously. That is how the dashboard shows real progress without
holding anything privileged.

### The rate limit

Refusal is decided from the published data, not from the caller: if a domain
was scraped within its minimum interval (PSX 30 minutes, MUFAP 20) the request
is refused with `429`. The harm worth preventing is a redundant scrape of
someone else's website, and that is the same harm whether one caller causes it
twenty times or twenty callers cause it once. It also means the limit needs no
KV namespace and no state in the worker.

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
