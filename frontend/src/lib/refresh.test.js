/**
 * Tests for the on-demand refresh flow.
 *
 * This is the piece that cannot be exercised by hand without a token that
 * writes to the repository, so it is the piece most worth testing with a fake
 * one. Every GitHub and Pages response is stubbed, so these run offline and
 * assert the things a real refresh would only reveal in production:
 *
 *   - the dispatch body is exactly what refresh.yml's resolver accepts
 *   - progress reflects the real run, and never reports Live before the CDN
 *     is actually serving new bytes
 *   - a failed run surfaces as a failure rather than hanging
 *   - aborting stops every poll, so a closed panel leaves nothing running
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  REPO,
  dispatchRefresh,
  runRefresh,
  snapshotFetchTimes,
  waitForNewData,
} from './refresh'

// Production waits seconds between polls; the state machine is identical at
// one millisecond, so the tests drive it at that and finish instantly.
const FAST = { findRun: 1, runPoll: 1, cdnPoll: 1 }

const RUN_ID = 4242
const RUN_URL = 'https://github.com/Sohaib-Sarwar/PSX-MUFAP-MicroService/actions/runs/4242'

/** A scripted GitHub + Pages endpoint set. */
function stubServer({
  dispatchStatus = 204,
  runStates = ['queued', 'in_progress', 'completed'],
  conclusion = 'success',
  step = { job: 'psx', name: 'Refresh psx', status: 'in_progress' },
  freshnessSequence = [],
} = {}) {
  const calls = { dispatch: [], runs: 0, jobs: 0, freshness: 0 }
  let runStateIndex = 0
  let freshnessIndex = 0

  const json = (body, status = 200) =>
    Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    })

  global.fetch = vi.fn((url, init = {}) => {
    const target = String(url)

    if (target.endsWith('/v1/refresh')) {
      calls.dispatch.push({ body: JSON.parse(init.body), headers: init.headers })
      return json(
        dispatchStatus === 204 || dispatchStatus === 200
          ? { dispatched: JSON.parse(init.body).domain, consumer: 'dashboard' }
          : { error: 'nope', retry_after_seconds: { mufap: 600 } },
        dispatchStatus === 204 ? 200 : dispatchStatus
      )
    }

    if (target.includes('/actions/runs?')) {
      calls.runs += 1
      return json({
        workflow_runs: [
          { id: RUN_ID, created_at: new Date().toISOString(), html_url: RUN_URL },
        ],
      })
    }

    if (target.includes(`/actions/runs/${RUN_ID}/jobs`)) {
      calls.jobs += 1
      return json({ jobs: [{ name: step.job, steps: [step] }] })
    }

    if (target.includes(`/actions/runs/${RUN_ID}`)) {
      const status = runStates[Math.min(runStateIndex, runStates.length - 1)]
      runStateIndex += 1
      return json({ id: RUN_ID, status, conclusion, html_url: RUN_URL })
    }

    if (target.includes('freshness.json')) {
      calls.freshness += 1
      const body = freshnessSequence[Math.min(freshnessIndex, freshnessSequence.length - 1)]
      freshnessIndex += 1
      return json(body ?? { datasets: {} })
    }

    throw new Error(`unstubbed fetch: ${target}`)
  })

  return calls
}

const OLD = { 'psx.stocks': '2026-09-25T21:17:00+05:00' }
const NEW = { datasets: { 'psx.stocks': { fetched_at: '2026-09-28T17:05:00+05:00' } } }
const UNCHANGED = { datasets: { 'psx.stocks': { fetched_at: '2026-09-25T21:17:00+05:00' } } }

afterEach(() => {
  vi.restoreAllMocks()
})

describe('repository configuration', () => {
  it('derives owner and repo from the configured URL', () => {
    // If this ever parses to empty strings every request 404s against
    // api.github.com/repos// and the panel fails with a confusing message.
    expect(REPO.owner).toBe('Sohaib-Sarwar')
    expect(REPO.repo).toBe('PSX-MUFAP-MicroService')
  })
})

describe('dispatchRefresh', () => {
  it('asks the refresh API, not GitHub', async () => {
    const calls = stubServer()
    await dispatchRefresh('mufap', 'key', undefined)

    expect(calls.dispatch).toHaveLength(1)
    expect(calls.dispatch[0].body.domain).toBe('mufap')
    // The browser must never talk to api.github.com to *start* a run — that
    // would mean it was holding a credential that can write to the repository.
    const started = global.fetch.mock.calls.map(([url]) => String(url))
    expect(started.some((url) => url.includes('api.github.com/repos') && url.includes('dispatch')))
      .toBe(false)
  })

  it.each(['psx', 'mufap', 'both'])('accepts the %s domain', async (domain) => {
    const calls = stubServer()
    await dispatchRefresh(domain, 'key')
    expect(calls.dispatch[0].body.domain).toBe(domain)
  })

  it('explains a rejected key in plain words', async () => {
    stubServer({ dispatchStatus: 401 })
    await expect(dispatchRefresh('psx', 'bad')).rejects.toThrow(/was not accepted/)
  })

  it('turns a throttle into a wait the user can act on', async () => {
    stubServer({ dispatchStatus: 429 })
    await expect(dispatchRefresh('mufap', 'key')).rejects.toThrow(/Try again in about 10 minutes/)
  })

  it('sends the refresh key as a bearer credential', async () => {
    const calls = stubServer()
    await dispatchRefresh('psx', 'key-123')
    expect(calls.dispatch[0].headers.Authorization).toBe('Bearer key-123')
  })
})

describe('runRefresh', () => {
  it('reports every stage in order and finishes on Live', async () => {
    stubServer({ freshnessSequence: [NEW] })
    const stages = []

    const result = await runRefresh({
      domain: 'psx',
      token: 'tok',
      before: OLD,
      timings: FAST,
      onProgress: ({ stage }) => stages.push(stage),
    })

    expect(stages[0]).toBe('dispatch')
    expect(stages).toContain('queued')
    expect(stages.at(-1)).toBe('live')
    expect(result.runUrl).toBe(RUN_URL)
  })

  it('does not claim success until the published data has moved', async () => {
    // The run goes green immediately but Pages keeps serving the old file for
    // two polls. Reporting Live on the green run would be the bug that makes a
    // refresh look like it did nothing.
    stubServer({
      runStates: ['completed'],
      freshnessSequence: [UNCHANGED, UNCHANGED, NEW],
    })
    const stages = []
    await runRefresh({
      domain: 'psx',
      token: 'tok',
      before: OLD,
      timings: FAST,
      onProgress: ({ stage }) => stages.push(stage),
    })

    const live = stages.lastIndexOf('live')
    const publishing = stages.lastIndexOf('publishing')
    expect(publishing).toBeLessThan(live)
    expect(stages.at(-1)).toBe('live')
  })

  it('surfaces a failed run instead of waiting forever', async () => {
    stubServer({ runStates: ['completed'], conclusion: 'failure' })
    await expect(
      runRefresh({ domain: 'psx', token: 'tok', before: OLD, timings: FAST })
    ).rejects.toThrow(/finished as "failure"/)
  })

  it('names the fetch count stage so the panel can say what is happening', async () => {
    stubServer({
      runStates: ['in_progress', 'completed'],
      step: { job: 'psx', name: 'Refresh psx', status: 'in_progress' },
      freshnessSequence: [NEW],
    })
    const seen = []
    await runRefresh({
      domain: 'psx',
      token: 'tok',
      before: OLD,
      timings: FAST,
      onProgress: (progress) => seen.push(progress),
    })
    expect(seen.some((p) => p.stage === 'scraping')).toBe(true)
  })
})

describe('cancellation', () => {
  it('stops immediately when the panel is closed mid-run', async () => {
    // A panel closed halfway must leave no poll behind. Without this the
    // dashboard leaks one GitHub poller per click, from a tab nobody is
    // looking at.
    stubServer({ runStates: ['queued'], freshnessSequence: [UNCHANGED] })
    const controller = new AbortController()

    const promise = runRefresh({
      domain: 'psx',
      token: 'tok',
      before: OLD,
      signal: controller.signal,
      timings: FAST,
      onProgress: () => controller.abort(),
    })

    await expect(promise).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('waitForNewData rejects on abort rather than resolving stale', async () => {
    stubServer({ freshnessSequence: [UNCHANGED] })
    const controller = new AbortController()
    const promise = waitForNewData(OLD, controller.signal, 3, FAST)
    controller.abort()
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('gives up with an explanation rather than hanging forever', async () => {
    stubServer({ freshnessSequence: [UNCHANGED] })
    await expect(waitForNewData(OLD, undefined, 2, FAST)).rejects.toThrow(
      /has not changed yet/
    )
  })
})

describe('snapshotFetchTimes', () => {
  it('returns the current fetch time per dataset', async () => {
    stubServer({ freshnessSequence: [NEW] })
    const times = await snapshotFetchTimes()
    expect(times['psx.stocks']).toBe('2026-09-28T17:05:00+05:00')
  })

  it('bypasses the HTTP cache', async () => {
    // Pages serves freshness.json with max-age=600. Reading it through the
    // cache would compare the new data against a ten-minute-old baseline and
    // conclude nothing had changed.
    stubServer({ freshnessSequence: [NEW] })
    await snapshotFetchTimes()
    const [url, init] = global.fetch.mock.calls[0]
    expect(String(url)).toMatch(/[?&]t=\d+/)
    expect(init.cache).toBe('no-store')
  })

  it('returns an empty baseline instead of throwing when Pages is down', async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error('offline')))
    await expect(snapshotFetchTimes()).resolves.toEqual({})
  })
})
