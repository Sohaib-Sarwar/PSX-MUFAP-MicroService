/**
 * Worker logic, tested on plain node with no dependencies: `npm test`.
 *
 * Covers the three things that decide whether this worker is safe and correct,
 * none of which can be exercised without either waiting for a cron or holding
 * a credential:
 *
 *   domainFor      which upstream each scheduled fire refreshes
 *   identify       key matching, including the cases that would authorise the
 *                  wrong caller
 *   applyThrottle  when a refresh is refused as redundant
 */
import assert from 'node:assert/strict'
import { applyThrottle, domainFor, identify } from './worker.js'

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


// ── auth ────────────────────────────────────────────────────────────────────

const KEYS = JSON.stringify({ dashboard: 'key-dash-1234', partner: 'key-part-5678' })

assert.equal(identify('key-dash-1234', KEYS), 'dashboard', 'valid key identifies its consumer')
assert.equal(identify('key-part-5678', KEYS), 'partner', 'second key works too')
assert.equal(identify('key-dash-123', KEYS), null, 'a prefix is not a match')
assert.equal(identify('key-dash-12345', KEYS), null, 'a superstring is not a match')
assert.equal(identify('', KEYS), null, 'empty key rejected')
assert.equal(identify(undefined, KEYS), null, 'missing key rejected')
assert.equal(identify('anything', 'not json at all'), null, 'malformed API_KEYS authorises nobody')
assert.equal(identify('anything', undefined), null, 'absent API_KEYS authorises nobody')
assert.equal(identify('legacy-secret', undefined, 'legacy-secret'), 'legacy', 'TRIGGER_SECRET still accepted')
assert.equal(identify('', undefined, ''), null, 'an empty legacy secret is not a key')
checks += 10

// ── throttle ────────────────────────────────────────────────────────────────
// The rate limit is expressed in terms of the data, not the caller: the harm is
// a redundant scrape of someone else's site, however many callers cause it.
let t = applyThrottle('mufap', { mufap: 5 }, false)
assert.deepEqual(t.allowed, [], 'fetched 5m ago, minimum 20m -> refused')
assert.ok(t.retryAfter.mufap > 0 && t.retryAfter.mufap <= 15 * 60, 'retry-after is the remaining wait')

t = applyThrottle('mufap', { mufap: 25 }, false)
assert.deepEqual(t.allowed, ['mufap'], 'past the interval -> allowed')

t = applyThrottle('mufap', { mufap: 5 }, true)
assert.deepEqual(t.allowed, ['mufap'], 'force overrides the throttle')

t = applyThrottle('both', { psx: 5, mufap: 60 }, false)
assert.deepEqual(t.allowed, ['mufap'], 'both: only the due domain runs')
assert.ok(t.retryAfter.psx > 0, 'and the other reports its wait')

t = applyThrottle('both', {}, false)
assert.deepEqual(t.allowed, ['psx', 'mufap'], 'unknown ages allow the refresh')
checks += 7

console.log(`worker total: ${checks} checks passed`)
