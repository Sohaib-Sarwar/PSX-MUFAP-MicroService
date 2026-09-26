/**
 * PK Finance refresh API.
 *
 * Two jobs.
 *
 * 1. Punctual scheduling. GitHub's `schedule` event is best effort — measured
 *    on this repository over five working days it delivered the PSX run four
 *    to six hours late every day and one or two of seven requested MUFAP runs.
 *    Cloudflare's cron triggers fire within seconds and `repository_dispatch`
 *    starts a run within seconds of the POST, so this worker is the join
 *    between the two.
 *
 * 2. Holding the GitHub token so nobody else has to. Triggering a workflow
 *    needs a credential that can write to the repository. The token lives here
 *    in a Cloudflare secret and never leaves — not to a browser, not to a
 *    caller, not into a response body.
 *
 * ── Why /v1/refresh has no key ───────────────────────────────────────────────
 * A static dashboard cannot hold a secret: anything shipped to the browser is
 * public by definition, so a key there would be security theatre with a login
 * prompt attached. The endpoint is open instead, and made safe by refusing to
 * do anything pointless: it reads the published freshness file and declines if
 * the data was fetched more recently than it could possibly have changed.
 *
 * That bounds the real cost. PSX publishes one closing board per trading day,
 * so a second refresh four hours later cannot return different numbers and is
 * refused. MUFAP posts NAV at an unpredictable evening hour, so twenty minutes
 * is the shortest interval that can carry news. An open endpoint can therefore
 * cause at most six PSX and seventy-two MUFAP runs a day even under sustained
 * abuse, and a normal caller is never told no.
 *
 * Overriding the throttle is deliberately *not* possible here. Forcing a
 * refresh means running the workflow from the Actions tab, where GitHub has
 * already authenticated you.
 *
 * Deploy with `npm run login && npm run secrets && npm run deploy`. Use the npm
 * scripts rather than npx: this repository's path contains an `&`, which
 * cmd.exe treats as a command separator.
 */

const OWNER = 'Sohaib-Sarwar'
const REPO = 'PSX-MUFAP-MicroService'
const SITE = 'https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService'
const FRESHNESS_URL = `${SITE}/api/freshness.json`

const DOMAINS = ['psx', 'mufap', 'both']

/**
 * The shortest interval over which each source can produce different numbers.
 *
 * This is the rate limit, and it is expressed in terms of the data rather than
 * the caller on purpose: the harm worth preventing is a redundant scrape of
 * someone else's website, and that harm is identical whether one caller causes
 * it twenty times or twenty callers cause it once. Reading the published
 * freshness file also means the limit needs no KV namespace, no Durable Object
 * and no state in this worker at all.
 */
const MIN_INTERVAL_MINUTES = {
  psx: 240,  // one closing board per trading day; sooner cannot differ
  mufap: 20, // NAV lands at an unpredictable evening hour
}

const DATASET_FOR = { psx: 'psx.stocks', mufap: 'mufap.funds' }

const log = (fields) => console.log(JSON.stringify(fields))
const logError = (fields) => console.error(JSON.stringify(fields))

/**
 * Which domain a given UTC time wants refreshed.
 *
 * PSX publishes one closing board per trading day and MUFAP strikes NAV once
 * per business day but posts it at no fixed hour, so the evening is swept. The
 * hours here are the same ones the workflow crons ask GitHub for; the point of
 * this worker is that these actually happen.
 */
export function domainFor(date) {
  const day = date.getUTCDay() // 0 Sun .. 6 Sat
  const hour = date.getUTCHours()

  if (day === 0 || day === 6) return null // PSX and MUFAP both rest
  if (hour === 12) return 'psx' // 17:00 PKT, after the close
  if (hour >= 13 && hour <= 19) return 'mufap' // 18:00 -> 00:00 PKT
  return null
}

/** Which of the requested domains are due, and how long the rest must wait. */
export function applyThrottle(domain, ages) {
  const wanted = domain === 'both' ? ['psx', 'mufap'] : [domain]
  const allowed = []
  const retryAfter = {}

  for (const name of wanted) {
    const age = ages[name]
    const minimum = MIN_INTERVAL_MINUTES[name]
    if (age === undefined || age >= minimum) {
      allowed.push(name)
    } else {
      retryAfter[name] = Math.ceil((minimum - age) * 60)
    }
  }
  return { allowed, retryAfter }
}

/** Minutes since each domain was last fetched, read from the published API. */
async function fetchAges() {
  try {
    const response = await fetch(`${FRESHNESS_URL}?t=${Date.now()}`, {
      cf: { cacheTtl: 0 },
    })
    if (!response.ok) return {}
    // freshness.json is a fixed handful of fields, well under a kilobyte —
    // bounded by construction, so reading it whole is safe.
    const body = await response.json()
    const ages = {}
    for (const [domain, dataset] of Object.entries(DATASET_FOR)) {
      const at = body?.datasets?.[dataset]?.fetched_at
      if (at) ages[domain] = (Date.now() - Date.parse(at)) / 60000
    }
    return ages
  } catch (error) {
    // If the freshness file cannot be read, allow the refresh. Refusing
    // because a throttle could not be read would turn a CDN blip into an
    // outage of the one mechanism that exists to recover from outages.
    logError({ message: 'freshness read failed', error: String(error) })
    return {}
  }
}

async function dispatch(domain, token) {
  if (!token) throw new Error('The worker has no GITHUB_TOKEN configured.')

  const response = await fetch(
    `https://api.github.com/repos/${OWNER}/${REPO}/dispatches`,
    {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': `${OWNER}-pk-finance-refresh`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        event_type: 'refresh',
        client_payload: { domain, source: 'refresh-api' },
      }),
    }
  )

  // 204 No Content is success for this endpoint. The upstream body is never
  // read or forwarded: it is not this worker's to relay, and an error page is
  // a poor place to discover that something upstream reflected a header.
  if (response.status !== 204) {
    throw new Error(
      response.status === 401 || response.status === 403
        ? "The worker's GitHub token is missing, expired, or lacks Contents: write."
        : `GitHub rejected the dispatch (${response.status}).`
    )
  }
}

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
}

const json = (body, status = 200, extra = {}) =>
  Response.json(body, { status, headers: { ...CORS, ...extra } })

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime)
    const domain = domainFor(now)

    if (!domain) {
      log({ message: 'nothing scheduled', at: now.toISOString() })
      return
    }

    // The scheduled path is not throttled. It fires at the times the service
    // promises, and those times are already the interval.
    ctx.waitUntil(
      dispatch(domain, env.GITHUB_TOKEN)
        .then(() => log({ message: 'dispatched', domain, at: now.toISOString() }))
        .catch((error) =>
          logError({ message: 'scheduled dispatch failed', domain, error: error.message })
        )
    )
  },

  async fetch(request, env) {
    const url = new URL(request.url)

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS })
    }

    if (url.pathname === '/' || url.pathname === '/v1') {
      return json({
        service: 'PK Finance refresh API',
        docs: `https://github.com/${OWNER}/${REPO}#refresh-on-demand`,
        data: `${SITE}/api/`,
        endpoints: {
          'POST /v1/refresh': 'Scrape now. No credential required.',
          'GET /v1/health': 'Liveness.',
        },
        domains: DOMAINS,
        min_interval_minutes: MIN_INTERVAL_MINUTES,
      })
    }

    if (url.pathname === '/v1/health') {
      return json({ status: 'ok', at: new Date().toISOString() })
    }

    if (url.pathname !== '/v1/refresh') {
      return json({ error: 'Not found.' }, 404)
    }

    let payload = {}
    if (request.method === 'POST') {
      payload = await request.json().catch(() => ({}))
    }
    const domain = String(
      payload.domain || url.searchParams.get('domain') || 'both'
    ).toLowerCase()

    if (!DOMAINS.includes(domain)) {
      return json({ error: `domain must be one of ${DOMAINS.join(', ')}.` }, 400)
    }

    const ages = await fetchAges()
    const { allowed, retryAfter } = applyThrottle(domain, ages)

    if (allowed.length === 0) {
      const wait = Math.max(...Object.values(retryAfter))
      log({ message: 'throttled', domain, retry_after_seconds: wait })
      return json(
        {
          error: 'That data was refreshed too recently to have changed.',
          retry_after_seconds: retryAfter,
          hint: `Run the workflow from ${SITE.replace(
            'sohaib-sarwar.github.io',
            'github.com'
          )}/actions to override.`,
        },
        429,
        { 'Retry-After': String(wait) }
      )
    }

    const requested = allowed.length === 2 ? 'both' : allowed[0]
    try {
      await dispatch(requested, env.GITHUB_TOKEN)
    } catch (error) {
      logError({ message: 'dispatch failed', domain: requested, error: error.message })
      return json({ error: error.message }, 502)
    }

    log({ message: 'dispatched', domain: requested, skipped: Object.keys(retryAfter) })

    return json({
      dispatched: requested,
      skipped: retryAfter,
      at: new Date().toISOString(),
      // Public, and needs no credential, so a caller can follow what it started.
      track: {
        runs: `https://api.github.com/repos/${OWNER}/${REPO}/actions/runs?event=repository_dispatch&per_page=1`,
        freshness: FRESHNESS_URL,
        typical_seconds: 110,
      },
    })
  },
}
