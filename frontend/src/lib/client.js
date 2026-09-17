/**
 * Data client.
 *
 * The deployed dashboard reads the same static JSON tree that the public API
 * serves — there is no private endpoint behind it. Every dataset arrives whole
 * and once; filtering, sorting, ranking and pagination all happen in the
 * browser. That is not a shortcut, it is the shape the data has: one snapshot
 * per working day is small enough to hold, and holding it makes every control
 * on the page instant instead of a round trip.
 *
 * Setting VITE_API_BASE points the same screens at a running FastAPI instance
 * instead, which is how the stack is developed locally. Both modes return the
 * identical envelope, so nothing above this file knows which one is in use.
 */

const trim = (value) => String(value || '').replace(/\/+$/, '')

const LIVE_BASE = trim(import.meta.env.VITE_API_BASE)
export const IS_LIVE = Boolean(LIVE_BASE)

// import.meta.env.BASE_URL is what Vite was built with — '/' locally, '/<repo>/'
// on GitHub Pages. Deriving the API root from it means the same bundle works at
// either path with no rebuild-time URL baked in.
const STATIC_BASE = `${trim(import.meta.env.BASE_URL) || ''}/api`

export const API_BASE = LIVE_BASE || STATIC_BASE

export const REPO_URL =
  import.meta.env.VITE_REPO_URL || 'https://github.com/Sohaib-Sarwar/PSX-MUFAP-MicroService'

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/* Each resource names its static file and, for the live service, the query that
   returns the same rows. Anything not listed is simply not part of the UI. */
const RESOURCES = {
  catalog: { static: '/index.json', live: null },
  stocks: { static: '/psx/stocks.json', live: '/api/psx/stocks?limit=5000&traded_only=false' },
  summary: { static: '/psx/stocks/summary.json', live: '/api/psx/stocks/summary' },
  sectors: { static: '/psx/sectors.json', live: null },
  indices: { static: '/psx/indices.json', live: '/api/psx/indices' },
  marketStatus: { static: '/psx/market-status.json', live: '/api/psx/market-status' },
  funds: { static: '/mufap/funds.json', live: '/api/mufap/funds?limit=5000' },
  fundStats: { static: '/mufap/funds/stats.json', live: '/api/mufap/funds/stats' },
  categories: { static: '/mufap/funds/categories.json', live: '/api/mufap/funds/categories' },
  amcs: { static: '/mufap/funds/amcs.json', live: '/api/mufap/funds/amcs' },
}

const cache = new Map()
const inflight = new Map()

function urlFor(name) {
  const resource = RESOURCES[name]
  if (!resource) throw new ApiError(`Unknown resource '${name}'`, 0)
  // The catalog describes the published static API, so it always comes from
  // the static tree — a live FastAPI instance has OpenAPI instead and would
  // have nothing to answer with here.
  if (IS_LIVE && resource.live) return `${API_BASE}${resource.live}`
  return `${STATIC_BASE}${resource.static}`
}

async function request(url) {
  let response
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } })
  } catch (cause) {
    throw new ApiError('Could not reach the data source. Check your connection.', 0)
  }
  if (!response.ok) {
    // 404 on a static host means the dataset has never been published — worth
    // saying plainly rather than as a bare status code.
    const message =
      response.status === 404
        ? 'This dataset has not been published yet.'
        : `The data source answered ${response.status}.`
    throw new ApiError(message, response.status)
  }
  try {
    return await response.json()
  } catch {
    throw new ApiError('The data source returned something that was not JSON.', response.status)
  }
}

/** Fetch a named resource. Repeat calls in one session are served from memory. */
export function load(name, { fresh = false } = {}) {
  if (!fresh && cache.has(name)) return Promise.resolve(cache.get(name))
  if (inflight.has(name)) return inflight.get(name)

  const promise = request(urlFor(name))
    .then((body) => {
      cache.set(name, body)
      inflight.delete(name)
      return body
    })
    .catch((error) => {
      inflight.delete(name)
      throw error
    })

  inflight.set(name, promise)
  return promise
}

export function loadAll(names, options) {
  return Promise.all(names.map((name) => load(name, options)))
}

export function clearCache() {
  cache.clear()
  inflight.clear()
}

export function absoluteUrl(path) {
  const clean = String(path || '').replace(/^\//, '')
  if (/^https?:/i.test(STATIC_BASE)) return `${STATIC_BASE}/${clean}`
  if (typeof window === 'undefined') return `${STATIC_BASE}/${clean}`
  return new URL(`${STATIC_BASE}/${clean}`.replace(/\/$/, ''), window.location.origin).href
}

/**
 * Recompute freshness in the browser.
 *
 * A static file's `age_seconds` is frozen at the moment it was written — it
 * cannot tick. The publisher also emits `fetched_at` and `stale_after_seconds`
 * precisely so a consumer can work out the real age, and `stale_after_seconds`
 * is derived from the publication schedule, so a snapshot taken on Friday is
 * still fresh on Sunday rather than being called stale for the crime of a
 * weekend.
 */
export function freshnessNow(freshness) {
  if (!freshness) return null
  const fetchedAt = freshness.fetched_at ? Date.parse(freshness.fetched_at) : NaN
  if (Number.isNaN(fetchedAt)) return freshness

  const age = (Date.now() - fetchedAt) / 1000
  let state = freshness.state
  if (!freshness.record_count) state = 'unavailable'
  else if (freshness.error) state = 'degraded'
  else if (freshness.stale_after_seconds != null) {
    state = age > freshness.stale_after_seconds ? 'stale' : 'fresh'
  }

  return { ...freshness, age_seconds: age, state }
}
