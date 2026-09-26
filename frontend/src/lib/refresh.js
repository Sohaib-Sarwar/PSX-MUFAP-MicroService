/**
 * On-demand refresh: fire a scrape and follow it to the published site.
 *
 * Two halves, with very different permissions:
 *
 *   Firing  goes through the refresh API, which holds the GitHub token server
 *           side. The browser presents nothing at all. It used to present a
 *           GitHub PAT, which was wrong twice over: that token can rewrite the
 *           repository, and a static site cannot keep a secret anyway —
 *           anything shipped to the browser is public by definition. The API
 *           is open instead and protects itself by refusing refreshes that
 *           cannot return different data.
 *
 *   Watching needs nothing at all. The repository is public, so run status,
 *           job steps and the published freshness file are all readable
 *           anonymously — which is why the progress shown is the real run
 *           rather than a timer pretending to be one.
 *
 * Every poll here takes an AbortSignal and every wait is cancellable. A panel
 * closed mid-refresh must leave no interval, no timeout and no in-flight fetch
 * behind; a dashboard that leaks one poller per click ends up hammering the
 * GitHub API from a tab nobody is looking at.
 */

import { REPO_URL } from './client'

const API = 'https://api.github.com'

/** The refresh API. It holds the GitHub token; the browser never sees one. */
export const REFRESH_ENDPOINT = (
  import.meta.env.VITE_REFRESH_ENDPOINT ||
  'https://pk-finance-cron.pk-microservice.workers.dev'
).replace(/\/$/, '')

/**
 * Poll intervals, in milliseconds. Overridable so tests can drive the whole
 * state machine in a few milliseconds instead of a few minutes — a flow with
 * this many waits is otherwise only testable by waiting.
 */
export const TIMINGS = {
  findRun: 2000,      // how often to look for the run our dispatch created
  runPoll: 3000,      // how often to re-read the run and its job steps
  cdnPoll: 5000,      // how often to re-read freshness.json after the run
}

/** "https://github.com/owner/repo" -> { owner, repo } */
function repoParts() {
  const match = /github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/.exec(REPO_URL || '')
  return match ? { owner: match[1], repo: match[2] } : { owner: '', repo: '' }
}

export const REPO = repoParts()
export const ACTIONS_URL = `${REPO_URL}/actions/workflows/refresh.yml`

/** Cancellable sleep — rejects on abort so the caller's loop unwinds. */
function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'))
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/**
 * Read from GitHub's public API.
 *
 * Reads only, and anonymous by design: the repository is public, so run status
 * and job steps need no credential. This function deliberately has no way to
 * send one — an Authorization header here would mean the browser was holding
 * something it should not.
 */
async function gh(path, { signal } = {}) {
  const response = await fetch(`${API}${path}`, {
    signal,
    headers: {
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
  })
  const body = await response.json().catch(() => null)

  if (!response.ok) {
    const error = new Error(
      response.status === 403
        ? 'GitHub is rate limiting anonymous reads. Progress will catch up shortly.'
        : body?.message || `GitHub answered ${response.status}.`
    )
    error.status = response.status
    throw error
  }
  return body
}

/**
 * Fire the refresh through the refresh API.
 *
 * Resolves with what the API dispatched, which is not always what was asked
 * for: it refuses a domain scraped within its minimum interval, so requesting
 * "both" can legitimately come back having run only one.
 */
export async function dispatchRefresh(domain, signal) {
  let response
  try {
    response = await fetch(`${REFRESH_ENDPOINT}/v1/refresh`, {
      method: 'POST',
      signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain }),
    })
  } catch {
    throw new Error(
      'Could not reach the refresh API. Check your connection, or that the ' +
        'worker is deployed.'
    )
  }

  const body = await response.json().catch(() => null)

  if (response.status === 429) {
    // Not an error the user caused — the data simply cannot have changed yet.
    const seconds = Math.max(...Object.values(body?.retry_after_seconds || { a: 0 }))
    const minutes = Math.ceil(seconds / 60)
    const when = minutes >= 60
      ? `about ${Math.round(minutes / 60)} hour${minutes >= 90 ? 's' : ''}`
      : `about ${minutes} minute${minutes === 1 ? '' : 's'}`
    throw new Error(
      `Already up to date. The source has not published anything new — try ` +
        `again in ${when}.`
    )
  }
  if (!response.ok) {
    throw new Error(body?.error || `The refresh API answered ${response.status}.`)
  }
  return body
}

/**
 * Find the run our dispatch created.
 *
 * `repository_dispatch` returns 204 with no run id, so the run has to be found
 * by looking for one created after the POST. Polling starts immediately because
 * GitHub usually has the run within a second or two.
 */
async function findRun(startedAt, signal, timings = TIMINGS) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const body = await gh(
      `/repos/${REPO.owner}/${REPO.repo}/actions/runs` +
        `?event=repository_dispatch&per_page=5`,
      { signal }
    ).catch(() => null)

    const run = (body?.workflow_runs || []).find(
      (candidate) => Date.parse(candidate.created_at) >= startedAt - 5000
    )
    if (run) return run
    await wait(timings.findRun, signal)
  }
  return null
}

const STAGES = [
  { id: 'dispatch', label: 'Requesting refresh' },
  { id: 'queued', label: 'Queued on GitHub' },
  { id: 'scraping', label: 'Fetching from the source' },
  { id: 'publishing', label: 'Publishing the API' },
  { id: 'live', label: 'Live' },
]

export { STAGES }

/**
 * Run the whole refresh and report progress as it happens.
 *
 * `onProgress({ stage, label, detail, percent })` is called on every change.
 * Resolves with the new freshness once the published data has actually moved,
 * which is the only definition of "done" that matters to a consumer.
 */
export async function runRefresh({
  domain,
  signal,
  onProgress,
  before,
  timings = TIMINGS,
}) {
  const report = (stage, label, detail, percent) =>
    onProgress?.({ stage, label, detail, percent })

  const startedAt = Date.now()
  report('dispatch', 'Requesting refresh…', `domain: ${domain}`, 5)
  const accepted = await dispatchRefresh(domain, signal)

  report(
    'queued',
    'Queued on GitHub…',
    `dispatched ${accepted?.dispatched || domain} — waiting for a runner`,
    12
  )
  const run = await findRun(startedAt, signal, timings)
  if (!run) {
    throw new Error(
      'The refresh was accepted but no run appeared. Check the Actions tab.'
    )
  }

  // Follow the run. Job step names are the honest source of "what is happening
  // now" — inventing stages from a timer would be theatre.
  let runUrl = run.html_url
  let lastStep = ''
  for (let tick = 0; tick < 300; tick += 1) {
    const current = await gh(
      `/repos/${REPO.owner}/${REPO.repo}/actions/runs/${run.id}`,
      { signal }
    ).catch(() => null)

    if (current) {
      runUrl = current.html_url
      if (current.status === 'completed') {
        if (current.conclusion !== 'success') {
          throw new Error(
            `The refresh run finished as "${current.conclusion}". See ${runUrl}`
          )
        }
        break
      }
      if (current.status === 'in_progress') {
        const jobs = await gh(
          `/repos/${REPO.owner}/${REPO.repo}/actions/runs/${run.id}/jobs`,
          { signal }
        ).catch(() => null)

        const active = (jobs?.jobs || [])
          .flatMap((job) =>
            (job.steps || []).map((step) => ({ job: job.name, ...step }))
          )
          .find((step) => step.status === 'in_progress')

        if (active) {
          lastStep = `${active.job}: ${active.name}`
          const scraping = /refresh|fetch|scrape/i.test(active.name)
          report(
            scraping ? 'scraping' : 'publishing',
            scraping ? 'Fetching from the source…' : 'Publishing the API…',
            lastStep,
            scraping ? 45 : 72
          )
        } else {
          report('scraping', 'Working…', lastStep || 'run in progress', 40)
        }
      } else {
        report('queued', 'Queued on GitHub…', current.status, 15)
      }
    }
    await wait(timings.runPoll, signal)
  }

  // The run being green is not the same as the CDN serving the new bytes.
  report('publishing', 'Waiting for the CDN…', 'GitHub Pages is republishing', 85)
  const fresh = await waitForNewData(before, signal, 60, timings)
  report('live', 'Live', 'published data updated', 100)
  return { freshness: fresh, runUrl }
}

/**
 * Poll the published freshness file until a fetch time moves past `before`.
 *
 * Cache-busted deliberately: Pages sets max-age=600, and without this the
 * browser would happily serve the pre-refresh copy for ten minutes and make a
 * successful refresh look like it did nothing.
 */
export async function waitForNewData(before, signal, attempts = 60, timings = TIMINGS) {
  const { API_BASE } = await import('./client')
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}/freshness.json?t=${Date.now()}`, {
        cache: 'no-store',
        signal,
      })
      if (response.ok) {
        const body = await response.json()
        const moved = Object.entries(body.datasets || {}).some(([name, state]) => {
          const previous = before?.[name]
          return (
            state.fetched_at &&
            (!previous || Date.parse(state.fetched_at) > Date.parse(previous))
          )
        })
        if (moved) return body
      }
    } catch (error) {
      if (error.name === 'AbortError') throw error
    }
    await wait(timings.cdnPoll, signal)
  }
  throw new Error(
    'The run finished but the published data has not changed yet. GitHub Pages ' +
      'can take a minute to serve a new deploy — reload shortly.'
  )
}

/** Current fetch times, for comparing against after a refresh. */
export async function snapshotFetchTimes() {
  const { API_BASE } = await import('./client')
  try {
    const response = await fetch(`${API_BASE}/freshness.json?t=${Date.now()}`, {
      cache: 'no-store',
    })
    if (!response.ok) return {}
    const body = await response.json()
    return Object.fromEntries(
      Object.entries(body.datasets || {}).map(([name, state]) => [
        name,
        state.fetched_at,
      ])
    )
  } catch {
    return {}
  }
}
