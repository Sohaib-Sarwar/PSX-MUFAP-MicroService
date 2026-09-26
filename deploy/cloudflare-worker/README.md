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
npx wrangler login          # opens your browser once, to authorise Cloudflare
npm run secrets             # prompts for GITHUB_TOKEN, then TRIGGER_SECRET
npm run deploy
```

**The token** is a fine-grained personal access token scoped to this one
repository with a single permission — *Repository permissions → Contents: Read
and write*. That is the least GitHub accepts for `repository_dispatch`. It
cannot read your other repositories and it cannot act on your account.

Free tier covers this comfortably: 12 invocations a working day, each a single
outbound request.

## Check it works

```bash
curl "https://pk-finance-cron.<your-subdomain>.workers.dev/?key=$TRIGGER_SECRET&domain=mufap"
# {"dispatched":"mufap","at":"..."}
```

Then watch **Actions → Refresh on demand** in the repository.

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
