/**
 * PK Finance refresh API.
 *
 * Two jobs, and the second is the one that matters for security.
 *
 * 1. Punctual scheduling. GitHub's `schedule` event is best effort — measured
 *    on this repository over five working days it delivered the PSX run four
 *    to six hours late every day and one or two of seven requested MUFAP runs.
 *    Cloudflare's cron triggers fire within seconds and `repository_dispatch`
 *    starts a run within seconds of the POST, so this worker is the join
 *    between the two.
 *
 * 2. Holding the GitHub token so nobody else has to. Triggering a workflow
 *    needs a credential that can write to the repository. Handing that to every
 *    caller — a browser, an external service — hands every caller the ability
 *    to rewrite the repository, to do a job whose entire scope is "scrape now".
 *
 *    So the token lives here, in a Cloudflare secret, and never leaves. Callers
 *    present a refresh key instead: a credential that does exactly one thing,
 *    is issued per consumer, and is revoked individually without touching
 *    anything else. A leaked refresh key causes an unnecessary scrape. A leaked
 *    GitHub PAT causes an incident.
 *
 * Deploy with `npm run login && npm run secrets && npm run deploy` from this
 * directory. Use the npm scripts rather than npx: this repository's path
 * contains an `&`, which cmd.exe treats as a command separator.
 *
 * Secrets:
 *   GITHUB_TOKEN    fine-grained PAT, Contents: Read and write, this repo only
 *   API_KEYS        JSON object of {"consumer-name": "key"} — see README
 *   TRIGGER_SECRET  optional; still accepted, reported as the consumer "legacy"
 */

const OWNER = 'Sohaib-Sarwar'
const REPO = 'PSX-MUFAP-MicroService'
const SITE = 'https://sohaib-sarwar.github.io/PSX-MUFAP-MicroService'
const FRESHNESS_URL = `${SITE}/api/freshness.json`

const DOMAINS = ['psx', 'mufap', 'both']

/**
 * How recently a domain must have been fetched for a refresh to be refused.
 *
 * This is the rate limit, and it is deliberately expressed in terms of the data
 * rather than the caller: the harm worth preventing is a redundant scrape of
 * someone else's website, and that harm is the same whether it comes from one
 * caller twenty times or twenty callers once. Reading the published freshness
 * file also means the limit needs no KV namespace, no Durable Object, and no
 * state in this worker at all.
 */
const MIN_INTERVAL_MINUTES = { psx: 30, mufap: 20 }

const DATASET_FOR = { psx: 'psx.stocks', mufap: 'mufap.funds' }

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

/**
 * Match a presented key against the configured set, in constant time.
 *
 * Returns the consumer's name, or null. A plain `===` leaks the key a character
 * at a time to anyone able to measure response times, and every candidate is
 * compared rather than short-circuiting on the first match for the same reason.
 */
export function identify(presented, apiKeysJson, legacySecret) {
  if (!presented) return null

  let keys = {}
  try {
    keys = apiKeysJson ? JSON.parse(apiKeysJson) : {}
  } catch {
    // A malformed API_KEYS must not silently authorise everyone.
    keys = {}
  }
  if (legacySecret) keys.legacy = legacySecret

  let matched = null
  for (const [name, value] of Object.entries(keys)) {
    if (typeof value === 'string' && value && timingSafeEqual(presented, value)) {
      matched = name
    }
  }
  return matched
}

function timingSafeEqual(a, b) {
  // Every character of the longer string is compared either way, so the time
  // taken does not depend on where the first difference falls.
  const length = Math.max(a.length, b.length)
  let different = a.length === b.length ? 0 : 1
  for (let i = 0; i < length; i += 1) {
    different |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0)
  }
  return different === 0
}

/** Minutes since each domain was last fetched, read from the published API. */
async function fetchAges() {
  try {
    const response = await fetch(`${FRESHNESS_URL}?t=${Date.now()}`, {
      cf: { cacheTtl: 0 },
    })
    if (!response.ok) return {}
    const body = await response.json()
    const ages = {}
    for (const [domain, dataset] of Object.entries(DATASET_FOR)) {
      const at = body?.datasets?.[dataset]?.fetched_at
      if (at) ages[domain] = (Date.now() - Date.parse(at)) / 60000
    }
    return ages
  } catch {
    // If the freshness file cannot be read, allow the refresh. Refusing
    // because a throttle could not be read would turn a CDN blip into an
    // outage of the one mechanism that exists to recover from outages.
    return {}
  }
}

/** Which of the requested domains are due, and how long the rest must wait. */
export function applyThrottle(domain, ages, force) {
  const wanted = domain === 'both' ? ['psx', 'mufap'] : [domain]
  if (force) return { allowed: wanted, retryAfter: {} }

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

  // 204 No Content is success for this endpoint.
  if (response.status !== 204) {
    // The upstream body is deliberately not echoed to the caller — it is not
    // this worker's to forward, and an error page is a poor place to discover
    // that something upstream reflected a header.
    throw new Error(
      response.status === 401 || response.status === 403
        ? "The worker's GITHUB_TOKEN is missing, expired, or lacks Contents: write."
        : `GitHub rejected the dispatch (${response.status}).`
    )
  }
}

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type',
  'Access-Control-Max-Age': '86400',
}

const json = (body, status = 200, extra = {}) =>
  Response.json(body, { status, headers: { ...CORS, ...extra } })

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime)
    const domain = domainFor(now)
    if (!domain) {
      console.log(`${now.toISOString()}: nothing scheduled for this hour`)
      return
    }
    // waitUntil so a slow GitHub response cannot truncate the invocation.
    ctx.waitUntil(
      dispatch(domain, env.GITHUB_TOKEN)
        .then(() => console.log(`${now.toISOString()}: dispatched ${domain}`))
        .catch((error) => console.error(`dispatch failed: ${error.message}`))
    )
  },

  async fetch(request, env) {
    const url = new URL(request.url)

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS })
    }

    // An unauthenticated description of the service. It reveals nothing a
    // reader of the public repository does not already know.
    if (url.pathname === '/' || url.pathname === '/v1') {
      return json({
        service: 'PK Finance refresh API',
        docs: `https://github.com/${OWNER}/${REPO}#refresh-on-demand`,
        data: `${SITE}/api/`,
        endpoints: {
          'POST /v1/refresh':
            'Trigger a scrape. Authorization: Bearer <refresh key>.',
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

    // The key may arrive as a bearer token, or as ?key= for a one-line curl.
    // Both are matched against the same set.
    const header = request.headers.get('Authorization') || ''
    const presented =
      header.replace(/^Bearer\s+/i, '').trim() ||
      url.searchParams.get('key') ||
      ''

    if (!env.API_KEYS && !env.TRIGGER_SECRET) {
      return json(
        { error: 'No refresh keys are configured. Set the API_KEYS secret.' },
        503
      )
    }

    const consumer = identify(presented, env.API_KEYS, env.TRIGGER_SECRET)
    if (!consumer) {
      return json({ error: 'Unauthorized. Present a valid refresh key.' }, 401)
    }

    let payload = {}
    if (request.method === 'POST') {
      payload = await request.json().catch(() => ({}))
    }
    const domain = String(
      payload.domain || url.searchParams.get('domain') || 'both'
    ).toLowerCase()
    const force =
      payload.force === true || url.searchParams.get('force') === 'true'

    if (!DOMAINS.includes(domain)) {
      return json({ error: `domain must be one of ${DOMAINS.join(', ')}.` }, 400)
    }

    const ages = await fetchAges()
    const { allowed, retryAfter } = applyThrottle(domain, ages, force)

    if (allowed.length === 0) {
      const wait = Math.max(...Object.values(retryAfter))
      return json(
        {
          error: 'Already refreshed recently.',
          retry_after_seconds: retryAfter,
          hint: 'Send {"force": true} to override.',
        },
        429,
        { 'Retry-After': String(wait) }
      )
    }

    const requested = allowed.length === 2 ? 'both' : allowed[0]
    try {
      await dispatch(requested, env.GITHUB_TOKEN)
    } catch (error) {
      return json({ error: error.message }, 502)
    }

    return json({
      dispatched: requested,
      consumer,
      forced: Boolean(force),
      skipped: retryAfter,
      at: new Date().toISOString(),
      // Everything below is public and needs no credential, so a caller can
      // follow the run it just started.
      track: {
        runs: `https://api.github.com/repos/${OWNER}/${REPO}/actions/runs?event=repository_dispatch&per_page=1`,
        freshness: FRESHNESS_URL,
        typical_seconds: 110,
      },
    })
  },
}
