/**
 * The schedule mapping, tested.
 *
 * This decides which upstream every cron fire refreshes, and until now the
 * only way to exercise it was to wait for Cloudflare to fire one. It runs on
 * node with no dependencies: `node schedule.test.mjs`.
 */
import assert from 'node:assert/strict'
import { domainFor } from './worker.js'

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

console.log(`schedule mapping: ${checks} checks passed`)
