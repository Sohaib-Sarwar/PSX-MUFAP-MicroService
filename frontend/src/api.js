/**
 * API client.
 *
 * The base URL comes from VITE_API_BASE and defaults to the empty string,
 * meaning "same origin". That default is correct in both deployments:
 *   - Vercel: /api is a serverless function on the same domain
 *   - local:  Vite proxies /api to the Python service (see vite.config.js)
 * The previous client hardcoded https://api.fintraxa.com, so it could not be
 * pointed at localhost without editing source.
 */

const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

const FRESH_MS = 45_000     // serve from cache without asking
const STALE_MS = 5 * 60_000 // serve from cache but revalidate behind it

const cache = new Map()
const inflight = new Map()

class ApiError extends Error {
  constructor(message, status, code) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function parse(response) {
  let body = null
  try {
    body = await response.json()
  } catch {
    // fall through to the status-based message
  }
  if (!response.ok) {
    const detail = body?.error
    throw new ApiError(
      detail?.message || `Request failed (${response.status})`,
      response.status,
      detail?.code
    )
  }
  return body
}

function fetchFresh(path) {
  const promise = fetch(`${BASE}${path}`, { headers: { Accept: 'application/json' } })
    .then(parse)
    .then((data) => {
      cache.set(path, { data, at: Date.now() })
      inflight.delete(path)
      return data
    })
    .catch((error) => {
      inflight.delete(path)
      throw error
    })
  inflight.set(path, promise)
  return promise
}

async function get(path) {
  const entry = cache.get(path)
  const age = entry ? Date.now() - entry.at : Infinity

  if (entry && age < FRESH_MS) return entry.data

  if (entry && age < STALE_MS) {
    // Revalidate behind the cached answer; a failure here must not surface.
    if (!inflight.has(path)) fetchFresh(path).catch(() => {})
    return entry.data
  }

  if (inflight.has(path)) return inflight.get(path)

  try {
    return await fetchFresh(path)
  } catch (error) {
    // A network blip should not blank the screen if we have something usable.
    if (entry) return entry.data
    throw error
  }
}

const qs = (params) => {
  const search = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  })
  const rendered = search.toString()
  return rendered ? `?${rendered}` : ''
}

export function clearCache() {
  cache.clear()
  inflight.clear()
}

export const api = {
  ready: () => get('/ready'),

  // PSX
  stocks: (params) => get(`/api/psx/stocks${qs(params)}`),
  searchStocks: (q) => get(`/api/psx/stocks/search${qs({ q, limit: 50 })}`),
  gainers: (limit = 25) => get(`/api/psx/stocks/gainers${qs({ limit })}`),
  losers: (limit = 25) => get(`/api/psx/stocks/losers${qs({ limit })}`),
  active: (limit = 25) => get(`/api/psx/stocks/active${qs({ limit })}`),
  summary: () => get('/api/psx/stocks/summary'),
  indices: () => get('/api/psx/indices'),
  marketStatus: () => get('/api/psx/market-status'),

  // MUFAP
  funds: (params) => get(`/api/mufap/funds${qs(params)}`),
  searchFunds: (q) => get(`/api/mufap/funds/search${qs({ q, limit: 50 })}`),
  categories: () => get('/api/mufap/funds/categories'),
  fundStats: () => get('/api/mufap/funds/stats'),
  topFunds: (period = 'ytd', limit = 25) =>
    get(`/api/mufap/funds/top${qs({ period, limit })}`),
}

export { ApiError }
