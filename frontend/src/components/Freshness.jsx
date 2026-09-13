/**
 * Shows the freshness envelope the API returns on every response.
 *
 * This exists because the old dashboard could not tell the difference between
 * live prices and Friday's close re-fetched on a Sunday — both looked current.
 */
const LABEL = {
  fresh: 'Live',
  stale: 'Stale',
  degraded: 'Degraded',
  unavailable: 'No data',
}

function ago(seconds) {
  if (seconds == null) return ''
  if (seconds < 90) return `${Math.round(seconds)}s ago`
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

export default function Freshness({ freshness }) {
  if (!freshness) return null
  const { state, data_as_of: asOf, age_seconds: age, record_count: count } = freshness

  return (
    <div className={`fresh fresh--${state}`}>
      <span className="fresh-state">{LABEL[state] || state}</span>
      {asOf && (
        <span className="fresh-detail">
          data as of <strong>{String(asOf).replace('T', ' ').slice(0, 16)}</strong>
        </span>
      )}
      <span className="fresh-detail">fetched {ago(age)}</span>
      {count != null && <span className="fresh-detail">{count.toLocaleString()} records</span>}
      {freshness.error && <span className="fresh-error">{freshness.error}</span>}
    </div>
  )
}
