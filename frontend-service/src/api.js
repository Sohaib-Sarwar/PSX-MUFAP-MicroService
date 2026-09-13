const BASE = 'https://api.fintraxa.com'

/* ── Response Cache (stale-while-revalidate) ── */
const cache = new Map()
const inflight = new Map()
const CACHE_TTL = 2 * 60 * 1000   // 2 minutes — show cached data instantly
const STALE_TTL = 10 * 60 * 1000  // 10 minutes — revalidate in background after this

function getCacheKey(path, opts) {
  return `${opts?.method || 'GET'}:${path}`
}

async function request(path, opts = {}) {
  const key = getCacheKey(path, opts)
  const isGet = !opts.method || opts.method === 'GET'

  // For GET requests, check cache first
  if (isGet) {
    const cached = cache.get(key)
    if (cached) {
      const age = Date.now() - cached.time
      if (age < CACHE_TTL) {
        // Fresh cache — return immediately
        return cached.data
      }
      if (age < STALE_TTL) {
        // Stale but usable — return cached, revalidate in background
        revalidate(path, key)
        return cached.data
      }
    }

    // Deduplicate in-flight requests
    if (inflight.has(key)) {
      return inflight.get(key)
    }
  }

  const promise = fetch(`${BASE}${path}`, opts)
    .then(res => {
      if (!res.ok) throw new Error(`${res.status}`)
      return res.json()
    })
    .then(data => {
      if (isGet) {
        cache.set(key, { data, time: Date.now() })
      }
      inflight.delete(key)
      return data
    })
    .catch(err => {
      inflight.delete(key)
      // On network error, return stale cache if available
      const stale = cache.get(key)
      if (stale) return stale.data
      throw err
    })

  if (isGet) inflight.set(key, promise)
  return promise
}

function revalidate(path, key) {
  if (inflight.has(key)) return
  const p = fetch(`${BASE}${path}`)
    .then(res => res.ok ? res.json() : null)
    .then(data => {
      if (data) cache.set(key, { data, time: Date.now() })
      inflight.delete(key)
    })
    .catch(() => inflight.delete(key))
  inflight.set(key, p)
}

/** Clear cache for a prefix (called after scraping) */
function invalidate(prefix) {
  for (const key of cache.keys()) {
    if (key.includes(prefix)) cache.delete(key)
  }
}

/** Clear entire cache (used by pull-to-refresh) */
function clearAllCache() {
  cache.clear()
  inflight.clear()
}

export { clearAllCache }

export const api = {
  health:        ()  => request('/api/health'),

  // MUFAP
  getFunds:      (p) => request(`/api/mufap/funds?${new URLSearchParams(p || {})}`),
  searchFunds:   (q) => request(`/api/mufap/funds/search?q=${encodeURIComponent(q)}`),
  getFundStats:  ()  => request('/api/mufap/funds/stats'),
  getCategories: ()  => request('/api/mufap/funds/categories'),
  getTopNav:     (n) => request(`/api/mufap/funds/top-nav?limit=${n || 20}`),
  scrapeFunds:   ()  => request('/api/mufap/scrape/sync', { method: 'POST' }).then(r => { invalidate('/api/mufap/'); return r }),

  // PSX
  getStocks:     (p) => request(`/api/psx/stocks?${new URLSearchParams(p || {})}`),
  searchStocks:  (q) => request(`/api/psx/stocks/search?symbol=${encodeURIComponent(q)}`),
  getGainers:    (n) => request(`/api/psx/stocks/gainers?limit=${n || 20}`),
  getLosers:     (n) => request(`/api/psx/stocks/losers?limit=${n || 20}`),
  getActive:     (n) => request(`/api/psx/stocks/active?limit=${n || 20}`),
  getSummary:    ()  => request('/api/psx/stocks/summary'),
  getIndices:    ()  => request('/api/psx/indices'),
  scrapeStocks:  ()  => request('/api/psx/scrape/sync', { method: 'POST' }).then(r => { invalidate('/api/psx/'); return r }),
  scrapeIndices: ()  => request('/api/psx/scrape/indices', { method: 'POST' }).then(r => { invalidate('/api/psx/'); return r }),
}
