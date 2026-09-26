/**
 * On-demand refresh: fire a scrape and follow it to the published site.
 *
 * Two halves, with very different permissions:
 *
 *   Firing  needs a token, because `repository_dispatch` writes. The token is
 *           the viewer's own fine-grained PAT, kept in their browser's
 *           localStorage and sent to api.github.com and nowhere else. If they
 *           would rather not hold one, the panel falls back to deep-linking
 *           GitHub's own "Run workflow" button, which needs nothing.
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

const TOKEN_KEY = 'pkf.gh.token'

export function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function writeToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // Private browsing. The token simply will not persist past this tab.
  }
}

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

async function gh(path, { token, signal, ...init } = {}) {
  const headers = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    ...init.headers,
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`${API}${path}`, { ...init, headers, signal })
  if (response.status === 204) return null
  const body = await response.json().catch(() => null)

  if (!response.ok) {
    const message =
      response.status === 401 || response.status === 403
        ? 'GitHub rejected the token. It needs Contents: Read and write on this repository.'
        : body?.message || `GitHub answered ${response.status}.`
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  return body
}

/** Fire the refresh. Resolves once GitHub has accepted the dispatch. */
export async function dispatchRefresh(domain, token, signal) {
  if (!REPO.owner) throw new Error('Repository is not configured.')
  await gh(`/repos/${REPO.owner}/${REPO.repo}/dispatches`, {
    token,
    signal,
    method: 'POST',
    body: JSON.stringify({
      event_type: 'refresh',
      client_payload: { domain, source: 'dashboard' },
    }),
  })
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
  token,
  signal,
  onProgress,
  before,
  timings = TIMINGS,
}) {
  const report = (stage, label, detail, percent) =>
    onProgress?.({ stage, label, detail, percent })

  const startedAt = Date.now()
  report('dispatch', 'Requesting refresh…', `domain: ${domain}`, 5)
  await dispatchRefresh(domain, token, signal)

  report('queued', 'Queued on GitHub…', 'waiting for a runner', 12)
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
