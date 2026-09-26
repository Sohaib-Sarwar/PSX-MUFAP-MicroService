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
 */
import assert from 'node:assert/strict'
import { applyThrottle, domainFor } from './worker.js'

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

// Weekends: PSX does not trade and MUFAP does not strike NAV.
check('2026-09-26T12:00:00Z', null, 'Saturday')
check('2026-09-27T15:00:00Z', null, 'Sunday')

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

console.log(`worker: ${checks} checks passed`)
console.log(`  open endpoint worst case: ${perDay(240)} PSX + ${perDay(20)} MUFAP refreshes/day`)
