import { freshnessNow } from '../lib/client'
import {
  IconClock,
  IconDone,
  IconFetched,
  IconRecords,
  IconReload,
  IconWarn,
} from '../lib/icons'
import { ago, clock, day, moment, until } from '../lib/format'

/**
 * The freshness envelope, rendered.
 *
 * The fetch time leads, as an absolute instant, because that is the question
 * being asked: not "is this recent" but "which session am I looking at". For a
 * NAV struck once a business day those are different questions and only the
 * second one matters.
 */

const LABEL = { fresh: 'Current', stale: 'Overdue', degraded: 'Degraded', unavailable: 'No data' }
const ICON = { fresh: IconDone, stale: IconClock, degraded: IconWarn, unavailable: IconWarn }

export default function Freshness({ freshness, label }) {
  const live = freshnessNow(freshness)
  if (!live) return null

  const Icon = ICON[live.state] || IconClock
  // PSX stamps a trade timestamp; MUFAP a NAV validity date. Rendering a bare
  // date as a datetime invents a time it never had.
  const asOf = String(live.data_as_of || '')
  const asOfText = asOf.includes('T') ? moment(asOf) : day(asOf)

  return (
    <div className={`fresh fresh--${live.state}`}>
      <span className="fresh-state">
        <Icon aria-hidden="true" />
        {label ? `${label} · ` : ''}
        {LABEL[live.state] || live.state}
      </span>

      <span className="fresh-key" title={live.fetched_at || ''}>
        <IconFetched aria-hidden="true" style={{ width: 12, height: 12, verticalAlign: -2, marginRight: 5 }} />
        fetched <b>{clock(live.fetched_at)}</b> <span className="faint">({ago(live.age_seconds)})</span>
      </span>

      {live.data_as_of && (
        <span>
          priced <b>{asOfText}</b>
        </span>
      )}

      {live.next_refresh_at && (
        <span>
          <IconReload aria-hidden="true" style={{ width: 11, height: 11, verticalAlign: -1, marginRight: 4 }} />
          next <b>{until(live.next_refresh_at)}</b>
        </span>
      )}

      {live.record_count != null && (
        <span>
          <IconRecords aria-hidden="true" style={{ width: 11, height: 11, verticalAlign: -1, marginRight: 4 }} />
          <b>{live.record_count.toLocaleString()}</b> rows
        </span>
      )}

      {live.error && (
        <span className="fresh-err">
          Last refresh failed — showing the previous good snapshot. {live.error}
        </span>
      )}
    </div>
  )
}
