/**
 * Punctual trigger for the PK Finance scrapers.
 *
 * GitHub's `schedule` event is best effort. Measured on this repository over
 * five consecutive working days it delivered the PSX run four to six hours
 * late every day, and one or two of seven requested MUFAP runs. Nothing failed
 * and nothing was cancelled — the runs were simply never created.
 *
 * Cloudflare's cron triggers fire within seconds, and GitHub's
 * `repository_dispatch` starts a workflow run within seconds of the POST. This
 * worker is the join between the two, and it is what makes the published times
 * in the README true rather than aspirational.
 *
 * Deploy:
 *   npm create cloudflare@latest -- pk-finance-cron
 *   # replace src/index.js with this file, then:
 *   npx wrangler secret put GITHUB_TOKEN     # fine-grained PAT, see below
 *   npx wrangler deploy
 *
 * The token needs exactly one permission on this one repository:
 *   Repository permissions -> Contents: Read and write
 * That is the least GitHub will accept for repository_dispatch. It cannot read
 * your other repositories and it cannot act on your account.
 *
 * Schedules live in wrangler.toml, in UTC, and mirror the workflow crons.
 */

const OWNER = 'Sohaib-Sarwar'
const REPO = 'PSX-MUFAP-MicroService'

/**
 * Which domain a given UTC time wants refreshed.
 *
 * PSX publishes one closing board per trading day and MUFAP strikes NAV once
 * per business day but posts it at no fixed hour, so the evening is swept. The
 * hours here are the same ones the workflow crons ask GitHub for; the point of
 * this worker is that these actually happen.
 */
function domainFor(date) {
  const day = date.getUTCDay() // 0 Sun .. 6 Sat
  const hour = date.getUTCHours()

  if (day === 0 || day === 6) return null // PSX and MUFAP both rest
  if (hour === 12) return 'psx' // 17:00 PKT, after the close
  if (hour >= 13 && hour <= 19) return 'mufap' // 18:00 -> 00:00 PKT
  return null
}

async function dispatch(domain, token) {
  const response = await fetch(
    `https://api.github.com/repos/${OWNER}/${REPO}/dispatches`,
    {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': `${OWNER}-pk-finance-cron`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        event_type: 'refresh',
        client_payload: { domain, source: 'cloudflare-cron' },
      }),
    }
  )

  // 204 No Content is success for this endpoint.
  if (response.status !== 204) {
    throw new Error(`GitHub answered ${response.status}: ${await response.text()}`)
  }
}

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime)
    const domain = domainFor(now)
    if (!domain) {
      console.log(`${now.toISOString()}: nothing scheduled for this hour`)
      return
    }
    // waitUntil so a slow GitHub response cannot truncate the request.
    ctx.waitUntil(
      dispatch(domain, env.GITHUB_TOKEN)
        .then(() => console.log(`${now.toISOString()}: dispatched ${domain}`))
        .catch((error) => console.error(`dispatch failed: ${error.message}`))
    )
  },

  /**
   * A manual escape hatch: GET /?domain=mufap triggers a refresh now.
   *
   * Guarded by a shared secret because the worker holds a token that can write
   * to the repository. Without TRIGGER_SECRET set, the route refuses rather
   * than running — an open trigger is a way to drive unbounded traffic at
   * dps.psx.com.pk and mufap.com.pk from this deployment.
   */
  async fetch(request, env) {
    const url = new URL(request.url)
    const secret = env.TRIGGER_SECRET

    if (!secret) {
      return new Response('TRIGGER_SECRET is not configured.', { status: 503 })
    }
    if (url.searchParams.get('key') !== secret) {
      return new Response('Unauthorized.', { status: 401 })
    }

    const domain = url.searchParams.get('domain') || 'both'
    if (!['psx', 'mufap', 'both'].includes(domain)) {
      return new Response('domain must be psx, mufap or both.', { status: 400 })
    }

    try {
      await dispatch(domain, env.GITHUB_TOKEN)
    } catch (error) {
      return new Response(error.message, { status: 502 })
    }
    return Response.json({ dispatched: domain, at: new Date().toISOString() })
  },
}
