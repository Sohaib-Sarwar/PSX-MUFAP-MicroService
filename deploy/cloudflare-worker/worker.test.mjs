/**
 * Worker logic, tested on plain node with no dependencies: `npm test`.
 *
 * Covers the three things that decide whether this worker is safe and correct,
 * none of which can be exercised without either waiting for a cron or holding
 * a credential:
 *
 *   domainFor      which upstream each scheduled fire refreshes
 *   applyThrottle  when a refresh is refused as redundant, which is the only
 *                  thing standing between an open endpoint and an abusive
 *                  number of scrapes of someone else's website
 *   overdueDomains which domains a fire sweeps up for a fire that never
 *                  happened — and, more importantly, which it leaves alone
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { applyThrottle, domainFor, overdueDomains, stuckRuns, STUCK_MINUTES } from './worker.js'
import { parsePsxCompany, parseMufapTab, handleQuotes, num, isoDate, baseCategory, LIMITS, parseAmcPage, AMCS, fundNorm, psxQuotes } from './quotes.js'

const at = (iso) => new Date(iso)
let checks = 0
const check = (iso, expected, why) => {
  assert.equal(domainFor(at(iso)), expected, `${iso} (${why})`)
  checks += 1
}

// Monday 2026-09-28 is a working day.
check('2026-09-28T12:00:00Z', 'psx', '17:00 PKT, after the PSX close')
check('2026-09-28T13:00:00Z', 'mufap', '18:00 PKT, MUFAP window opens')
check('2026-09-28T16:00:00Z', 'mufap', '21:00 PKT, mid-window')
check('2026-09-28T19:00:00Z', 'mufap', '00:00 PKT, last slot')

// Outside the windows nothing should be refreshed.
check('2026-09-28T11:00:00Z', null, 'before the PSX slot')
check('2026-09-28T20:00:00Z', null, 'after the MUFAP window')
check('2026-09-28T03:00:00Z', null, 'middle of the night')

// Weekends: PSX does not trade, but MUFAP keeps posting NAVs, so its evening
// window runs every day.
check('2026-09-26T12:00:00Z', null, 'Saturday, no PSX')
check('2026-09-27T12:00:00Z', null, 'Sunday, no PSX')
check('2026-09-26T13:00:00Z', 'mufap', 'Saturday 18:00 PKT')
check('2026-09-27T15:00:00Z', 'mufap', 'Sunday 20:00 PKT')
check('2026-09-27T19:00:00Z', 'mufap', 'Sunday 00:00 PKT')
check('2026-09-27T20:00:00Z', null, 'Sunday after the window')

// Every weekday behaves identically.
for (const day of ['29', '30']) {
  check(`2026-09-${day}T12:00:00Z`, 'psx', 'weekday PSX slot')
  check(`2026-09-${day}T17:00:00Z`, 'mufap', 'weekday MUFAP slot')
}

// The two crons in wrangler.toml, expanded, must cover exactly 8 fires a day
// and map to the domains above — 1 PSX + 7 MUFAP.
const hours = [12, ...Array.from({ length: 7 }, (_, i) => 13 + i)]
const mapped = hours.map((h) => domainFor(at(`2026-09-28T${String(h).padStart(2, '0')}:00:00Z`)))
assert.equal(mapped.filter((d) => d === 'psx').length, 1, 'one PSX fire a day')
assert.equal(mapped.filter((d) => d === 'mufap').length, 7, 'seven MUFAP fires a day')
assert.equal(mapped.filter((d) => d === null).length, 0, 'no cron fire is wasted')
checks += 3


// ── throttle ────────────────────────────────────────────────────────────────
// The endpoint has no key, so this is the whole of its protection. The limit
// is derived from how often each source can actually produce different
// numbers: PSX publishes one closing board a trading day, MUFAP posts NAV at
// an unpredictable evening hour.

let t = applyThrottle('mufap', { mufap: 5 })
assert.deepEqual(t.allowed, [], 'fetched 5m ago, minimum 20m -> refused')
assert.ok(t.retryAfter.mufap > 0 && t.retryAfter.mufap <= 15 * 60, 'retry-after is the remaining wait')

t = applyThrottle('mufap', { mufap: 25 })
assert.deepEqual(t.allowed, ['mufap'], 'past the interval -> allowed')

t = applyThrottle('psx', { psx: 60 })
assert.deepEqual(t.allowed, [], 'PSX an hour old is refused; the board cannot have changed')

t = applyThrottle('psx', { psx: 300 })
assert.deepEqual(t.allowed, ['psx'], 'PSX past four hours -> allowed')

t = applyThrottle('both', { psx: 5, mufap: 60 })
assert.deepEqual(t.allowed, ['mufap'], 'both: only the due domain runs')
assert.ok(t.retryAfter.psx > 0, 'and the other reports its wait')

t = applyThrottle('both', {})
assert.deepEqual(t.allowed, ['psx', 'mufap'], 'unknown ages allow the refresh')
checks += 8

// Worst-case load an open endpoint can cause in a day, which is the number
// that has to be defensible rather than merely small.
const perDay = (minutes) => Math.floor((24 * 60) / minutes)
assert.ok(perDay(240) <= 6, `PSX capped at ${perDay(240)} refreshes a day`)
assert.ok(perDay(20) <= 72, `MUFAP capped at ${perDay(20)} refreshes a day`)
checks += 2


console.log(`  open endpoint worst case: ${perDay(240)} PSX + ${perDay(20)} MUFAP refreshes/day`)


/* ── catch-up ──────────────────────────────────────────────────────────────
 * Cloudflare delivered seven of eight fires on two days running and then none
 * at all on 2026-10-30, with the triggers still registered and every past
 * invocation green. A fire that never happens cannot notice it was missed, so
 * the next one checks. What it must never do is treat a source that refused a
 * fetch as a missed fire and ask again.
 */

const future = new Date(Date.now() + 3600_000).toISOString()
const past = new Date(Date.now() - 3600_000).toISOString()

const withFreshness = async (datasets, body) => {
  const real = globalThis.fetch
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ datasets }) })
  try {
    return await body()
  } finally {
    globalThis.fetch = real
  }
}

const expect = async (datasets, expected, why) => {
  const got = await withFreshness(datasets, () => overdueDomains())
  assert.deepEqual(got.sort(), expected.sort(), why)
  checks += 1
}

await expect(
  { 'psx.stocks': { state: 'fresh', stale_at: future },
    'mufap.funds': { state: 'fresh', stale_at: future } },
  [], 'nothing overdue, nothing swept up'
)

await expect(
  { 'psx.stocks': { state: 'fresh', stale_at: past },
    'mufap.funds': { state: 'fresh', stale_at: future } },
  ['psx'], 'a domain past its deadline is swept up'
)

await expect(
  { 'psx.stocks': { state: 'degraded', stale_at: past },
    'mufap.funds': { state: 'fresh', stale_at: future } },
  [], 'a source that refused the fetch is left alone, however overdue'
)

await expect(
  { 'psx.stocks': { state: 'fresh', stale_at: past, error: 'upstream said no' },
    'mufap.funds': { state: 'fresh', stale_at: future } },
  [], 'an error marks it blocked even where state does not'
)

await expect(
  { 'psx.stocks': { state: 'fresh' }, 'mufap.funds': { state: 'fresh' } },
  [], 'an envelope with no stale_at makes no claim either way'
)

await expect(
  { 'psx.stocks': { state: 'fresh', stale_at: past, overdue_seconds: 0, on_schedule: true },
    'mufap.funds': { state: 'fresh', stale_at: future } },
  ['psx'], 'the frozen build-time verdict is ignored in favour of stale_at'
)

{
  // A freshness file that cannot be read must not strand the fire's own
  // dispatch, which happens after this and does not depend on it.
  const real = globalThis.fetch
  globalThis.fetch = async () => ({ ok: false })
  assert.deepEqual(await overdueDomains(), [], 'an unreadable envelope sweeps up nothing')
  globalThis.fetch = real
  checks += 1
}

/* ── /v1/quotes parsers, against the repository's captured pages ─────────────── */
{
  const fx = (f) => readFileSync(new URL(`../../tests/fixtures/${f}`, import.meta.url), 'utf8')
  const q = parsePsxCompany(fx('psx_company_ogdc.html'), 'OGDC')
  assert.equal(q.status, 'ok')
  assert.equal(q.price, 316.23, 'price from quote__close')
  assert.equal(q.as_of, '2026-09-24', 'as-of date from quote__date')
  assert.equal(parsePsxCompany('<html>no quote here</html>', 'XYZ').status, 'no_price', 'a page without a price is not a zero price')

  const want = [{ name: 'ABL Cash Fund', category: 'Money Market' }, { name: 'No Such Fund', category: 'Equity' }]
  const t1 = parseMufapTab(fx('mufap_tab1.html'), want)
  assert.deepEqual(t1.map((r) => [r.name, r.nav, r.validity_date]), [['ABL Cash Fund', 10.4862, '2026-09-14']], 'tab=1: NAV and validity; unknown funds are simply absent')
  const t3 = parseMufapTab(fx('mufap_tab3.html'), want)
  assert.equal(t3[0].offer_price, 10.5774, 'tab=3: the offer price, not the sales load')
  assert.equal(t3[0].nav, 10.4862)

  assert.equal(num('(6.21)'), -6.21); assert.equal(num('1,234.50'), 1234.5); assert.equal(num('N/A'), null)
  assert.equal(isoDate('Nov 06, 2026'), '2026-11-06'); assert.equal(isoDate('Tue, Nov 10, 2026 3:49 PM'), '2026-11-10')
  assert.equal(baseCategory('VPS-Debt (Annualized Return )'), 'VPS-Debt')

  // Inputs are capped before anything is fetched.
  assert.equal((await handleQuotes({ source: 'psx', symbols: Array.from({ length: LIMITS.symbols + 1 }, (_, i) => `S${i}`) }))[0], 400)
  assert.equal((await handleQuotes({ source: 'mufap', tab: 2, funds: want }))[0], 400)
  assert.equal((await handleQuotes({ source: 'nope' }))[0], 400)

  // A challenge is reported, not retried.
  const real = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => { calls += 1; return new Response('challenge', { status: 403, headers: { 'cf-mitigated': 'challenge', 'cf-ray': 'abc-LAX' } }) }
  const [, blocked] = await handleQuotes({ source: 'mufap', tab: 1, funds: want })
  globalThis.fetch = real
  assert.deepEqual([blocked.status, blocked.ray, blocked.funds.length, calls], ['blocked', 'abc-LAX', 0, 1])
  checks += 16
}

/* ── fund managers' own price pages ───────────────────────────────────────────── */
{
  const fx = (f) => readFileSync(new URL(`../../tests/fixtures/${f}`, import.meta.url), 'utf8')
  const ubl = AMCS['UBL Fund Managers Limited']
  const meezan = AMCS['Al Meezan Investment Management Limited']

  // MUFAP spells it "Al Ameen", UBL "Al-Ameen"; the fund is listed twice on UBL's page.
  assert.equal(fundNorm('Al-Ameen Islamic Cash Fund'), fundNorm('Al Ameen Islamic Cash Fund'))
  const u = parseAmcPage(fx('amc_ubl_prices.html'), ubl, ['Al Ameen Islamic Cash Fund', 'UBL Stock Advantage Fund', 'UBL Financial Sector Fund', 'No Such Fund'])
  const cash = u.find((r) => fundNorm(r.name) === fundNorm('Al Ameen Islamic Cash Fund'))
  assert.deepEqual([cash.validity_date, cash.price, cash.kind, cash.offer_price], ['2026-11-11', 105.1289, 'repurchase', 108.7558], 'UBL: redemption price and "Applicable on" date')
  assert.equal(u.length, 3, 'UBL: each wanted fund once; an unknown fund is absent')
  assert.ok(u.every((r) => /^\d{4}-\d{2}-\d{2}$/.test(r.validity_date) && r.price > 0))

  const m = parseAmcPage(fx('amc_meezan_prices.html'), meezan, ['Meezan Islamic Fund', 'KSE Meezan Index Fund', 'Al Meezan Mutual Fund', 'Meezan Rozana Amdani Fund'])
  const by = Object.fromEntries(m.map((r) => [r.name, r]))
  assert.deepEqual([by['Meezan Islamic Fund'].validity_date, by['Meezan Islamic Fund'].price, by['Meezan Islamic Fund'].kind], ['2026-11-10', 164.6799, 'repurchase'], 'Meezan: repurchase price')
  assert.equal(by['KSE Meezan Index Fund'].price, 183.2582)
  assert.equal(by['Al Meezan Mutual Fund'].price, 50.6503)
  // A money-market fund quotes no repurchase price (0.0000), only the NAV; and loses its "***".
  assert.deepEqual([by['Meezan Rozana Amdani Fund'].price, by['Meezan Rozana Amdani Fund'].kind], [50, 'nav'], 'Meezan: NAV where no repurchase price is quoted')
  assert.equal(parseAmcPage('<html>maintenance</html>', ubl, ['Al Ameen Islamic Cash Fund']).length, 0, 'a page without the table yields nothing, not zeros')

  // Fetch path: one page per manager; a manager not in the registry is reported, not guessed.
  const real = globalThis.fetch
  const seen = []
  globalThis.fetch = async (url) => { seen.push(String(url)); return new Response(fx(String(url).includes('ublfunds') ? 'amc_ubl_prices.html' : 'amc_meezan_prices.html'), { status: 200 }) }
  const [st, body] = await handleQuotes({ source: 'amc', funds: [
    { name: 'Al Ameen Islamic Cash Fund', amc: 'UBL Fund Managers Limited' },
    { name: 'UBL Stock Advantage Fund', amc: 'UBL Fund Managers Limited' },
    { name: 'Meezan Islamic Fund', amc: 'Al Meezan Investment Management Limited' },
    { name: 'Some Fund', amc: 'Unlisted AMC Limited' },
  ] })
  globalThis.fetch = real
  assert.equal(st, 200)
  assert.equal(seen.length, 2, 'one request per manager, not per fund')
  assert.equal(body.funds.length, 3)
  assert.deepEqual(body.unsupported, ['Unlisted AMC Limited'])
  assert.equal((await handleQuotes({ source: 'amc', funds: [] }))[0], 400)
  checks += 16
}

/* ── PSX: a timed-out page is asked once more; a 404 is an answer ────────────── */
{
  const fx = (f) => readFileSync(new URL(`../../tests/fixtures/${f}`, import.meta.url), 'utf8')
  const real = globalThis.fetch
  const tries = {}
  globalThis.fetch = async (url) => {
    const sym = String(url).split('/').pop()
    tries[sym] = (tries[sym] ?? 0) + 1
    if (sym === 'NOPE') return new Response('', { status: 404 })
    if (sym === 'SLOW' && tries[sym] === 1) throw new DOMException('timed out', 'TimeoutError')
    return new Response(fx('psx_company_ogdc.html'), { status: 200 })
  }
  const q = await psxQuotes(['OGDC', 'SLOW', 'NOPE'], { retryDelayMs: 0 })
  globalThis.fetch = real
  assert.deepEqual(q.map((x) => x.status), ['ok', 'ok', 'not_found'])
  assert.deepEqual([tries.OGDC, tries.SLOW, tries.NOPE], [1, 2, 1], 'only the failed symbol is retried, once')
  checks += 2
}

/* ── stuck runs ─────────────────────────────────────────────────────────────── */
{
  const now = Date.parse('2026-11-10T16:00:00Z')
  const runs = [
    { id: 1, status: 'waiting', created_at: '2026-11-09T18:00:52Z' },               // the real one
    { id: 2, status: 'waiting', created_at: new Date(now - 5 * 60000).toISOString() }, // just arrived
    { id: 3, status: 'in_progress', created_at: '2026-11-09T18:00:52Z' },            // not ours to judge
  ]
  assert.deepEqual(stuckRuns(runs, now).map((r) => r.id), [1], `only a run waiting over ${STUCK_MINUTES} minutes`)
  assert.deepEqual(stuckRuns(undefined, now), [])
  checks += 2
}

console.log(`worker: ${checks} checks passed`)
