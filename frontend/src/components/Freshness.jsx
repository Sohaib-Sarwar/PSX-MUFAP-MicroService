import { FiAlertCircle, FiClock, FiDatabase, FiRefreshCw, FiZap } from 'react-icons/fi'
import { freshnessNow } from '../lib/client'
import { ago, day, moment, until } from '../lib/format'

/**
 * The freshness envelope, rendered.
 *
 * Every response the service publishes carries one, and showing it is the
 * whole point: a dashboard that cannot distinguish live prices from Friday's
 * close re-read on a Sunday is a dashboard that quietly lies twice a week.
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
  // PSX reports a trade timestamp, MUFAP a NAV validity date. Formatting a
  // bare date as a datetime invents a time it never had — "18 Sept, 05:00"
  // for what the source published as 2026-09-18.
  const asOf = String(live.data_as_of || '')
  const asOfText = asOf.includes('T') ? moment(asOf) : day(asOf)

  return (
    <div className={`fresh fresh--${live.state}`}>
      <span className="fresh-state">
        <Icon aria-hidden="true" />
        {label ? `${label}: ` : ''}
        {LABEL[live.state] || live.state}
      </span>

      {live.data_as_of && (
        <span>
          data as of <b>{asOfText}</b>
        </span>
      )}

      <span>
        <FiRefreshCw
          aria-hidden="true"
          style={{ verticalAlign: '-2px', marginRight: 5, width: 12, height: 12 }}
        />
        fetched <b>{ago(live.age_seconds)}</b>
      </span>

      {live.next_refresh_at && (
        <span>
          next run <b>{until(live.next_refresh_at)}</b>
        </span>
      )}

      {live.record_count != null && (
        <span>
          <FiDatabase
            aria-hidden="true"
            style={{ verticalAlign: '-2px', marginRight: 5, width: 12, height: 12 }}
          />
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
