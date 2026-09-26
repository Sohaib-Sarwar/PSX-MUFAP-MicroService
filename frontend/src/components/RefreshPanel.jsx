import { useCallback, useEffect, useRef, useState } from 'react'
import {
  FiAlertTriangle,
  FiBarChart2,
  FiBriefcase,
  FiCheck,
  FiCheckCircle,
  FiCircle,
  FiExternalLink,
  FiKey,
  FiLayers,
  FiLoader,
  FiRefreshCw,
  FiX,
} from 'react-icons/fi'
import { ACTIONS_URL, STAGES, runRefresh, snapshotFetchTimes } from '../lib/refresh'
import { int } from '../lib/format'

const DOMAINS = [
  { id: 'both', label: 'Both', icon: FiLayers, note: 'PSX + MUFAP' },
  { id: 'psx', label: 'PSX', icon: FiBarChart2, note: 'equities' },
  { id: 'mufap', label: 'MUFAP', icon: FiBriefcase, note: 'funds' },
]

/**
 * Trigger a scrape now and watch it land.
 *
 * The progress shown is the real workflow run read from GitHub's public API,
 * not a timer. That matters: a refresh takes anywhere from forty seconds to
 * three minutes depending on how quickly a runner is free, and a bar that
 * guesses would be wrong in both directions.
 */
export default function RefreshPanel({ open, onClose, counts, onDone }) {
  const [domain, setDomain] = useState('both')
  const [state, setState] = useState({ phase: 'idle' })
  const abortRef = useRef(null)

  // One abort controller owns every poll, timer and fetch this panel starts.
  // Closing mid-run must leave nothing behind — a dashboard that leaks a
  // poller per click ends up hammering GitHub from a tab nobody is watching.
  useEffect(() => {
    return () => abortRef.current?.abort()
  }, [])

  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape' && state.phase !== 'running') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, state.phase])

  const start = useCallback(async () => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const expected =
      domain === 'psx'
        ? `${int(counts.psx)} instruments`
        : domain === 'mufap'
          ? `${int(counts.mufap)} funds`
          : `${int(counts.psx)} instruments and ${int(counts.mufap)} funds`

    setState({ phase: 'running', stage: 'dispatch', label: 'Starting…', percent: 4, expected })

    try {
      const before = await snapshotFetchTimes()
      const result = await runRefresh({
        domain,
        signal: controller.signal,
        before,
        onProgress: ({ stage, label, detail, percent }) =>
          setState((current) =>
            current.phase === 'running'
              ? { ...current, stage, label, detail, percent }
              : current
          ),
      })
      setState({ phase: 'done', runUrl: result.runUrl })
      onDone?.()
    } catch (error) {
      if (error.name === 'AbortError') return
      setState({ phase: 'error', message: error.message })
    }
  }, [domain, counts, onDone])

  const close = useCallback(() => {
    abortRef.current?.abort()
    setState({ phase: 'idle' })
    onClose()
  }, [onClose])

  if (!open) return null

  const running = state.phase === 'running'
  const stageIndex = STAGES.findIndex((s) => s.id === state.stage)

  return (
    <div
      className="sheet-scrim"
      role="dialog"
      aria-modal="true"
      aria-label="Refresh data"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !running) close()
      }}
    >
      <div className="sheet">
        <header className="sheet-head">
          <FiRefreshCw aria-hidden="true" style={{ width: 15, height: 15 }} />
          <h2>Refresh now</h2>
          <button
            type="button"
            className="btn btn--icon"
            onClick={close}
            aria-label="Close"
            disabled={running}
          >
            <FiX />
          </button>
        </header>

        <div className="sheet-body">
          {state.phase !== 'done' && (
            <div>
              <p className="sheet-label" style={{ marginBottom: 7 }}>
                What to fetch
              </p>
              <div className="choice-row">
                {DOMAINS.map((option) => {
                  const Icon = option.icon
                  return (
                    <button
                      key={option.id}
                      type="button"
                      className={`choice${domain === option.id ? ' is-active' : ''}`}
                      onClick={() => setDomain(option.id)}
                      disabled={running}
                      aria-pressed={domain === option.id}
                    >
                      <Icon aria-hidden="true" />
                      {option.label}
                      <small>{option.note}</small>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {running && (
            <div className="progress" role="status" aria-live="polite">
              <div className="progress-line">
                <FiLoader className="spin" aria-hidden="true" />
                {state.label}
              </div>
              <div className="progress-track">
                <div
                  className={
                    state.percent ? 'progress-fill' : 'progress-fill progress-fill--sweep'
                  }
                  style={state.percent ? { width: `${state.percent}%` } : undefined}
                />
              </div>
              <p className="progress-detail">
                {state.stage === 'scraping'
                  ? `Fetching ${state.expected} from the source…`
                  : state.detail || '…'}
              </p>
              <div className="progress-steps">
                {STAGES.map((stage, index) => {
                  const done = index < stageIndex
                  const active = index === stageIndex
                  return (
                    <div
                      key={stage.id}
                      className={`progress-step${done ? ' is-done' : ''}${active ? ' is-active' : ''}`}
                    >
                      {done ? <FiCheckCircle /> : <FiCircle />}
                      {stage.label}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {state.phase === 'done' && (
            <div className="callout callout--ok">
              <FiCheck aria-hidden="true" />
              <div>
                <strong>Refreshed.</strong> The published API now carries the new
                figures.{' '}
                {state.runUrl && (
                  <a href={state.runUrl} target="_blank" rel="noreferrer noopener">
                    View the run
                  </a>
                )}
              </div>
            </div>
          )}

          {state.phase === 'error' && (
            <div className="callout callout--warn">
              <FiAlertTriangle aria-hidden="true" />
              <div>{state.message}</div>
            </div>
          )}

          {!running && state.phase !== 'done' && (
            <div className="callout">
              <FiKey aria-hidden="true" />
              <div>
                Nothing to enter. The GitHub credential stays on the server; this
                just asks it to scrape. If the source has not published anything
                new since the last fetch, the request is declined rather than
                repeating a scrape that cannot return different numbers.
              </div>
            </div>
          )}
        </div>

        <footer className="sheet-foot">
          <span className="spacer" />
          {state.phase === 'done' ? (
            <button type="button" className="btn btn--primary" onClick={close}>
              Done
            </button>
          ) : (
            <>
              <button type="button" className="btn" onClick={close} disabled={running}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={start}
                disabled={running}
              >
                <FiRefreshCw aria-hidden="true" />
                {running ? 'Refreshing…' : 'Start refresh'}
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  )
}
