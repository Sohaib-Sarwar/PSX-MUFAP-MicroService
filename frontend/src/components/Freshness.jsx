import {
  FiAlertCircle,
  FiClock,
  FiDatabase,
  FiDownloadCloud,
  FiRefreshCw,
  FiZap,
} from 'react-icons/fi'
import { freshnessNow } from '../lib/client'
import { ago, clock, day, moment, until } from '../lib/format'

/**
 * The freshness envelope, rendered.
 *
 * Every response the service publishes carries one, and showing it is the whole
 * point: a dashboard that cannot distinguish live prices from Friday's close
 * re-read on a Sunday is a dashboard that quietly lies twice a week.
 *
 * The fetch time is shown as an absolute instant, not only as "12m ago".
 * Relative age answers "is this recent"; it does not answer "which session am I
 * looking at", and for a NAV struck once a business day that is the question
 * being asked.
 */

const LABEL = {
  fresh: 'Current',
  stale: 'Overdue',
  degraded: 'Degraded',
  unavailable: 'No data',
}

const ICON = {
  fresh: FiZap,
  stale: FiClock,
  degraded: FiAlertCircle,
  unavailable: FiAlertCircle,
}

export default function Freshness({ freshness, label }) {
  const live = freshnessNow(freshness)
  if (!live) return null

  const Icon = ICON[live.state] || FiClock
  // PSX reports a trade timestamp, MUFAP a NAV validity date. Formatting a bare
  // date as a datetime invents a time it never had.
  const asOf = String(live.data_as_of || '')
  const asOfText = asOf.includes('T') ? moment(asOf) : day(asOf)

  return (
    <div className={`fresh fresh--${live.state}`}>
      <span className="fresh-state">
        <Icon aria-hidden="true" />
        {label ? `${label}: ` : ''}
        {LABEL[live.state] || live.state}
      </span>

      <span className="fresh-primary" title={live.fetched_at || ''}>
        <FiDownloadCloud aria-hidden="true" />
        fetched <b>{clock(live.fetched_at)}</b>
        <span className="fresh-rel">({ago(live.age_seconds)})</span>
      </span>

      {live.data_as_of && (
        <span>
          priced <b>{asOfText}</b>
        </span>
      )}

      {live.next_refresh_at && (
        <span>
          <FiRefreshCw aria-hidden="true" className="fresh-ic" />
          next <b>{until(live.next_refresh_at)}</b>
        </span>
      )}

      {live.record_count != null && (
        <span>
          <FiDatabase aria-hidden="true" className="fresh-ic" />
          <b>{live.record_count.toLocaleString()}</b> records
        </span>
      )}

      {live.error && (
        <span className="fresh-error">
          Last refresh failed — showing the previous good snapshot. {live.error}
        </span>
      )}
    </div>
  )
}
