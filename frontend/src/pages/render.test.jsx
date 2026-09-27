/**
 * Every page renders, against the data that is actually published.
 *
 * The static check next door catches a name that resolves to nothing. This
 * catches the rest: a field read off the wrong shape, a helper handed a null it
 * does not guard, a map over something that is not an array. Both were needed —
 * the API reference page went blank behind a `status === 'ready'` branch, which
 * a loading-state render would have walked straight past.
 *
 * The fixtures are trimmed copies of the published files, kept next door in
 * `__fixtures__`, so the shapes are the real envelope rather than a
 * hand-written stand-in that drifts from it. They deliberately include the rows
 * that have broken something: instruments with and without a live quote, funds
 * on both return bases, and the rows whose market cap or offer price the source
 * does not publish.
 */
import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { renderToString } from 'react-dom/server'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '__fixtures__')

const read = (name) => JSON.parse(readFileSync(join(FIXTURES, `${name}.json`), 'utf8'))

// The pages ask for data by name; hand them the published file for that name.
vi.mock('../lib/hooks', async (importOriginal) => ({
  ...(await importOriginal()),
  useData: (names) => ({ status: 'ready', bodies: names.map(read) }),
  useDebounced: (value) => value,
}))

const { default: Overview } = await import('./Overview')
const { default: Stocks } = await import('./Stocks')
const { default: Indices } = await import('./Indices')
const { default: Funds } = await import('./Funds')
const { default: ApiDocs } = await import('./ApiDocs')
const { default: Landing } = await import('./Landing')
const { default: RefreshPanel } = await import('../components/RefreshPanel')

const PAGES = { Landing, Overview, Stocks, Indices, Funds, ApiDocs }

describe('pages render against the published API', () => {
  for (const [name, Page] of Object.entries(PAGES)) {
    it(`${name} renders`, () => {
      const html = renderToString(<Page nonce={0} onNavigate={() => {}} />)
      expect(html.length).toBeGreaterThan(200)
      // A page that threw would have been caught above; a page that rendered
      // its failure state is just as broken and says so quietly.
      expect(html).not.toContain('Could not load this')
    })
  }

  it('the refresh panel renders open, with both sources offered', () => {
    const html = renderToString(
      <RefreshPanel open counts={{ psx: 747, mufap: 553 }} onClose={() => {}} onDone={() => {}} />
    )
    expect(html).toMatch(/PSX/)
    expect(html).toMatch(/MUFAP/)
    // Keyless by design: the panel must never ask for a token or a refresh key.
    expect(html).not.toMatch(/token|api[_ -]?key|refresh key|secret/i)
  })

  it('the refresh panel renders nothing while closed', () => {
    const html = renderToString(
      <RefreshPanel open={false} counts={{ psx: 0, mufap: 0 }} onClose={() => {}} onDone={() => {}} />
    )
    expect(html).toBe('')
  })

  it('a price of zero renders as absent, but a load of zero does not', () => {
    // MUFAP publishes 0.0 for a price it does not quote. Rendering that as
    // "0.0000" beside real NAVs states a number the source never gave — and it
    // is a price, so a reader could act on it. A zero *load* is the opposite:
    // most funds genuinely charge nothing, and a dash would lose that.
    const html = renderToString(<Funds nonce={0} onNavigate={() => {}} />)
    expect(html).not.toMatch(/>0\.0000</)
    expect(html).toMatch(/>0\.00%</)
  })
})
