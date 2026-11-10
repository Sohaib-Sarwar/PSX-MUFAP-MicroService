/**
 * POST /v1/quotes — latest prices for a handful of instruments, fetched from the source now.
 *
 * The scheduled pipeline (GitHub Actions → static JSON) republishes the whole board on a
 * timetable and takes minutes. This is the other path: a caller that holds a few stocks and
 * funds asks for exactly those, and the worker fetches them itself, in seconds.
 *
 *   { "source": "psx",   "symbols": ["MEBL", "FFC"] }                       ≤ 40 symbols
 *   { "source": "mufap", "tab": 1, "funds": [{ "name": "…", "category": "…" }] }   ≤ 40 funds
 *
 * PSX: one company page per symbol (dps.psx.com.pk/company/SYM), six at a time.
 * MUFAP: one IndustryStatDaily tab per request (tab=1 NAV/returns, tab=3 prices), so each
 * request stays inside the Workers CPU budget; the caller asks for both tabs in parallel and
 * keeps whichever is newer per fund, as the scraper does.
 *
 * Bounded by construction, like /v1/refresh: no key, but capped inputs and the edge cache in
 * front of every upstream page (PSX 60 s, MUFAP 10 min), so however often it is called the
 * sources see at most one request per page per window.
 *
 * Plain requests, honestly identified. If a source refuses (MUFAP's bot management has been
 * challenging datacenter addresses), the answer says so and nothing is retried: this path is not
 * a way around anyone's firewall. The caller keeps the last published figure.
 */

const UA = 'Mozilla/5.0 (compatible; PK-Finance-Service/6.0; +https://github.com/Sohaib-Sarwar/PSX-MUFAP-MicroService)'
const PSX = 'https://dps.psx.com.pk'
const MUFAP = 'https://www.mufap.com.pk/Industry/IndustryStatDaily'
const TTL = { psx: 60, mufap: 600 }
export const LIMITS = { symbols: 40, funds: 40 }
const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 }

// ── text helpers ────────────────────────────────────────────────────────────

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', rsquo: '’', lsquo: '‘' }
export function decode(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : Number(e.slice(1)))
    return ENTITIES[e.toLowerCase()] ?? m
  })
}
const text = (html) => decode(html.replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim()

/** "1,234.50" → 1234.5; "(6.21)" → −6.21; blanks → null. */
export function num(s) {
  if (s === null || s === undefined) return null
  let t = String(s).trim()
  if (!t || /^(-|--|n\/a|na|nil)$/i.test(t)) return null
  const neg = /^\(.*\)$/.test(t)
  t = t.replace(/[(),%+\s]/g, '').replace(/^rs\.?/i, '')
  const v = Number(t)
  if (!Number.isFinite(v)) return null
  return neg ? -v : v
}

/** "Nov 10, 2026" → "2026-11-10". */
export function isoDate(s) {
  const m = /([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})/.exec(s ?? '')
  if (!m || !MONTHS[m[1].toLowerCase()]) return null
  return `${m[3]}-${String(MONTHS[m[1].toLowerCase()]).padStart(2, '0')}-${m[2].padStart(2, '0')}`
}

/** "Money Market (Annualized Return )" → "Money Market", as app/mufap/parsers.py does. */
export const baseCategory = (c) => String(c ?? '').replace(/\s*\((Annualized|Absolute)\s+Return\s*\)\s*$/i, '').trim()
const key = (name, category) => `${String(name).trim().toLowerCase()}|${baseCategory(category).toLowerCase()}`

// ── PSX ─────────────────────────────────────────────────────────────────────

/**
 * One company page → { symbol, name, price, change, change_pct, as_of, as_of_text }.
 * The quote block is a few labelled divs; each is read by its class, never by position.
 */
export function parsePsxCompany(html, symbol) {
  const grab = (cls) => {
    const m = new RegExp(`class="${cls}[^"]*"[^>]*>([\\s\\S]*?)</div>`).exec(html)
    return m ? text(m[1]) : null
  }
  const price = num(grab('quote__close'))
  if (price === null || price <= 0) return { symbol, status: 'no_price' }
  const negative = /quote__change[^"]*change__text--neg/.test(html)
  const change = num(grab('change__value'))
  const pct = num(grab('change__percent'))
  const asOfText = (grab('quote__date') ?? '').replace(/^\W*as of\s*/i, '').trim() || null
  const nameMatch = /class="quote__name"[^>]*>([^<]*)/.exec(html)
  return {
    symbol, status: 'ok',
    name: nameMatch ? decode(nameMatch[1]).trim() : null,
    price,
    change: change === null ? null : (negative ? -Math.abs(change) : Math.abs(change)),
    change_pct: pct === null ? null : (negative ? -Math.abs(pct) : Math.abs(pct)),
    as_of: isoDate(asOfText),
    as_of_text: asOfText,
  }
}

// ── MUFAP ───────────────────────────────────────────────────────────────────

/**
 * The rows of one IndustryStatDaily tab for the funds asked for, matched on name and base
 * category (VPS sub-funds share a name and differ only by category).
 * Cheap on purpose: a row is only taken apart if its HTML contains one of the wanted names.
 * → [{ name, category, nav, validity_date, repurchase_price, offer_price }]
 */
export function parseMufapTab(html, wanted) {
  const head = html.slice(html.indexOf('<thead'), html.indexOf('</thead>'))
  const headers = [...head.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map((m) => text(m[1]).toLowerCase())
  const col = (pred) => headers.findIndex(pred)
  const idx = {
    name: col((h) => h === 'fund name' || h === 'fund'),
    category: col((h) => h === 'category'),
    nav: col((h) => h === 'nav'),
    validity: col((h) => h === 'validity date'),
    repurchase: col((h) => h.startsWith('repurchase')),
    offer: col((h) => h === 'offer'),
  }
  if (idx.name < 0 || idx.nav < 0 || idx.validity < 0) throw new Error('MUFAP table layout not recognised')

  const want = new Map(wanted.map((f) => [key(f.name, f.category), f]))
  // Prefilters: the first 14 characters of each name as they appear in HTML.
  const probes = [...new Set(wanted.map((f) => String(f.name).trim().slice(0, 14).replace(/&/g, '&amp;')))]
  const body = html.slice(html.indexOf('<tbody'))
  const out = []
  for (const row of body.split('<tr').slice(1)) {
    if (!probes.some((p) => row.includes(p))) continue
    const cells = [...row.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((m) => text(m[1]))
    const name = cells[idx.name]; const category = idx.category >= 0 ? cells[idx.category] : ''
    if (!name || !want.has(key(name, category))) continue
    const nav = num(cells[idx.nav])
    if (nav === null || nav <= 0) continue
    const zeroIsNull = (v) => (v ? v : null) // MUFAP prints 0 where it quotes no price
    out.push({
      name, category: baseCategory(category), nav,
      validity_date: isoDate(cells[idx.validity]),
      repurchase_price: idx.repurchase >= 0 ? zeroIsNull(num(cells[idx.repurchase])) : null,
      offer_price: idx.offer >= 0 ? zeroIsNull(num(cells[idx.offer])) : null,
    })
  }
  return out
}

/** A Cloudflare challenge rather than the page. */
const challenged = (res) => res.status === 403 || res.status === 503 || res.headers.get('cf-mitigated') === 'challenge'

// ── fetching ────────────────────────────────────────────────────────────────

/** GET through the edge cache: at most one upstream request per URL per `ttl` seconds. */
async function cachedText(url, ttl, timeoutMs) {
  const cache = typeof caches !== 'undefined' ? caches.default : null
  const cacheKey = new Request(url)
  if (cache) {
    const hit = await cache.match(cacheKey)
    if (hit) return { status: 200, body: await hit.text(), cached: true }
  }
  const res = await fetch(url, {
    headers: { 'User-Agent': UA, Accept: 'text/html' },
    signal: AbortSignal.timeout(timeoutMs),
    cf: { cacheTtl: 0 },
  })
  if (!res.ok || challenged(res)) {
    return { status: res.status, body: null, ray: res.headers.get('cf-ray'), challenge: challenged(res) }
  }
  const body = await res.text()
  if (cache) {
    await cache.put(cacheKey, new Response(body, { headers: { 'Cache-Control': `public, max-age=${ttl}`, 'Content-Type': 'text/html' } }))
  }
  return { status: 200, body, cached: false }
}

async function pool(items, size, fn) {
  const out = new Array(items.length)
  let next = 0
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, async () => {
    while (next < items.length) { const i = next++; out[i] = await fn(items[i]) }
  }))
  return out
}

export async function psxQuotes(symbols) {
  return pool(symbols, 6, async (symbol) => {
    try {
      const r = await cachedText(`${PSX}/company/${encodeURIComponent(symbol)}`, TTL.psx, 12000)
      if (!r.body) return { symbol, status: r.status === 404 ? 'not_found' : 'unavailable', http: r.status }
      return { ...parsePsxCompany(r.body, symbol), cached: r.cached }
    } catch (error) {
      return { symbol, status: 'unavailable', error: String(error?.name ?? error) }
    }
  })
}

export async function mufapQuotes(tab, funds) {
  const r = await cachedText(`${MUFAP}?tab=${tab}`, TTL.mufap, 20000)
  if (!r.body) {
    return {
      status: r.challenge ? 'blocked' : 'unavailable', http: r.status, ray: r.ray ?? null,
      error: r.challenge ? 'MUFAP refused the request (Cloudflare challenge).' : `MUFAP answered ${r.status}.`,
      funds: [],
    }
  }
  return { status: 'ok', cached: r.cached, funds: parseMufapTab(r.body, funds) }
}

/** Validate and answer a /v1/quotes body. Returns [status, json]. */
export async function handleQuotes(payload) {
  const started = Date.now()
  if (payload?.source === 'psx') {
    const symbols = [...new Set((payload.symbols ?? []).map((s) => String(s).trim().toUpperCase()).filter((s) => /^[A-Z0-9-]{1,15}$/.test(s)))]
    if (!symbols.length || symbols.length > LIMITS.symbols) return [400, { error: `symbols: 1 to ${LIMITS.symbols} PSX symbols.` }]
    const quotes = await psxQuotes(symbols)
    return [200, { source: 'psx', fetched_at: new Date().toISOString(), ms: Date.now() - started, quotes }]
  }
  if (payload?.source === 'mufap') {
    const tab = Number(payload.tab)
    if (tab !== 1 && tab !== 3) return [400, { error: 'tab must be 1 or 3.' }]
    const funds = (payload.funds ?? []).filter((f) => f && typeof f.name === 'string' && f.name.trim())
      .map((f) => ({ name: f.name.slice(0, 160), category: String(f.category ?? '').slice(0, 120) }))
    if (!funds.length || funds.length > LIMITS.funds) return [400, { error: `funds: 1 to ${LIMITS.funds} { name, category }.` }]
    const result = await mufapQuotes(tab, funds)
    return [200, { source: 'mufap', tab, fetched_at: new Date().toISOString(), ms: Date.now() - started, ...result }]
  }
  return [400, { error: 'source must be "psx" or "mufap".' }]
}
