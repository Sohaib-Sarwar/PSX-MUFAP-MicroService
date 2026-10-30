# Requesting data access from MUFAP

Since roughly 2026-10-28, `www.mufap.com.pk` has served a Cloudflare managed
challenge to this service. It is site-wide — the homepage challenges too — and
it challenges an ordinary browser user-agent from a Pakistani residential
connection, not only from cloud addresses. It is a deliberate setting, so there
is no address to move to and no quieter endpoint to find.

Their `robots.txt` is Cloudflare-managed and reads:

```
User-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
Allow: /
```

with AI-training crawlers named individually and disallowed. So crawling is not
against their stated policy — the WAF is a separate control, and it is the one
that decides. That gap is what makes an allowlist request reasonable rather than
cheeky: their published policy already permits what the firewall is refusing.

There is **no official MUFAP API or bulk download**. Third-party scrapers of the
same pages exist on commercial platforms, which is itself evidence that no
sanctioned feed is on offer. So the options are to be allowlisted, to license
the data, or to take NAVs from each AMC directly.

---

## Draft message

Send to MUFAP's general contact, or to their IT/webmaster address if the site
lists one. Replace the bracketed parts.

> **Subject:** Request for data access — automated retrieval of daily NAV
> statistics blocked by bot management
>
> Dear MUFAP team,
>
> I maintain a small, non-commercial service that publishes Pakistani market
> data as a JSON API, including the daily NAV statistics MUFAP makes public at
> `/Industry/IndustryStatDaily`. It fetches that page **once an hour between
> 18:00 and 00:00 PKT on business days — at most seven requests a day** — and
> republishes the figures with clear attribution to MUFAP and a link back to
> your site.
>
> Since approximately 28 October 2026 those requests have been refused with
> HTTP 403 by Cloudflare's bot management. A representative example:
>
> ```
> URL:          https://www.mufap.com.pk/Industry/IndustryStatDaily?tab=1
> Status:       403
> cf-ray:       a525d36abbc8f018-LAX
> cf-mitigated: challenge
> Date:         2026-10-29 (and every attempt since)
> ```
>
> I want to be straightforward about two things. First, I am not trying to work
> around the challenge, which is why I am writing instead. Second, your
> `robots.txt` currently publishes `User-agent: *` with `Allow: /` and
> `Content-Signal: use=reference`, so I believe the retrieval is within the
> policy you have stated — the firewall rule appears to be catching it
> incidentally rather than by intent.
>
> Could you help with one of the following?
>
> 1. **Allowlist the service** in Cloudflare, by user-agent string or by source
>    address. My client identifies itself and I am happy to set any user-agent
>    you prefer. [If you can supply a fixed egress address, say so here.]
> 2. **Point me at a sanctioned feed** — an API, a CSV or Excel export, or a
>    daily file — if one exists or could be made available.
> 3. **Tell me the terms** under which republishing these figures is acceptable,
>    including any attribution or caching requirements, and I will comply.
>
> If automated access is simply not something MUFAP wishes to permit, please say
> so plainly and I will remove the MUFAP dataset from the service rather than
> continue requesting the page.
>
> Thank you for the public statistics you publish — they are genuinely useful.
>
> [Name]
> [Contact email]
> [Link to the service]

---

## While you wait

The service already degrades correctly and does not need changing:

- The last good snapshot (562 funds, dated 2026-10-28) is still served, as real
  data rather than a gap.
- `state` is `degraded` and `error` carries exactly what the source did, so a
  consumer can tell "stale" from "wrong".
- `stale_at` lets any consumer decide liveness for itself.
- The watchdog classifies this as `blocked`, not `late`, and deliberately does
  **not** re-dispatch — MUFAP's normal hourly slot still tries at the cadence it
  always has, and nothing retries on top of it.

So the data is visibly old rather than silently old, and MUFAP is not being
hammered while the question is settled.

## If the answer is no

Fall back to the AMCs. Most of the large managers — NBP, UBL, MCB, ABL, Meezan,
Al Meezan, Faysal, HBL, Atlas, JS — publish their own daily NAVs on their own
sites, and between them they cover the great majority of the 562 funds in the
snapshot. That is more parsers to maintain and more sources to keep honest, but
it removes the single point of failure entirely and each AMC is publishing its
own numbers, which is the authoritative place for them anyway.
