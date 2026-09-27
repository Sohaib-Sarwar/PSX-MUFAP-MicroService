/**
 * Formatting. Every number the dashboard renders passes through here, so a
 * missing value looks the same everywhere and nothing ever prints "NaN" or
 * "undefined" at a user.
 */

export const DASH = '—'

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)

/** Fixed decimals with thousands separators. */
export function num(value, dp = 2) {
  if (!isNum(value)) return DASH
  return value.toLocaleString('en-US', {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })
}

export function int(value) {
  return isNum(value) ? Math.round(value).toLocaleString('en-US') : DASH
}

/** 1.2B / 340.5M / 12.4K — for volumes and traded value. */
/**
 * Like compact(), but zero reads as absent.
 *
 * PSX publishes 0 for market capitalisation and free float on 76 of its 747
 * instruments — rights letters, non-compliant issues and the like. Zero there
 * means "not published", and printing it as `0` next to a real 1.4T is a
 * statement the source never made.
 */
/** Like num(), but zero reads as absent. See compactOrDash. */
export function numOrDash(value, dp = 2) {
  return value ? num(value, dp) : DASH
}

export function compactOrDash(value, dp = 1) {
  return value ? compact(value, dp) : DASH
}

export function compact(value, dp = 1) {
  if (!isNum(value)) return DASH
  const abs = Math.abs(value)
  if (abs >= 1e12) return `${(value / 1e12).toFixed(dp)}T`
  if (abs >= 1e9) return `${(value / 1e9).toFixed(dp)}B`
  if (abs >= 1e6) return `${(value / 1e6).toFixed(dp)}M`
  if (abs >= 1e3) return `${(value / 1e3).toFixed(dp)}K`
  return value.toLocaleString('en-US')
}

export function pct(value, dp = 2) {
  if (!isNum(value)) return DASH
  return `${value > 0 ? '+' : ''}${value.toFixed(dp)}%`
}

export function pkr(value) {
  if (!isNum(value)) return DASH
  return `Rs ${compact(value, 2)}`
}

/** 'up' | 'down' | 'flat' — the class suffix every coloured number uses. */
export function direction(value) {
  if (!isNum(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

/** "17 Sep 2026" from an ISO date or datetime. */
export function day(value) {
  if (!value) return DASH
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 10)
  return parsed.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

/** "17 Sep, 17:10" — a timestamp in Pakistan Standard Time, always. */
export function moment(value) {
  if (!value) return DASH
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Karachi',
  })
}

/** "25 Sep, 01:32" — compact, for a tile that must not wrap. */
export function clockShort(value) {
  if (!value) return DASH
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return DASH
  return parsed.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'Asia/Karachi',
  }).replace(',', '')
}

/** "25 Sep 2026, 01:32 PKT" — the absolute instant, always in Pakistan time.
 *
 * "4m ago" answers "is this recent"; it does not answer "which session is
 * this". For a NAV struck once a day, the second question is the one that
 * matters, so both are shown rather than only the relative one.
 */
export function clock(value) {
  if (!value) return DASH
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return `${parsed.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Karachi',
  })} PKT`
}

/** "4m ago" / "2d ago". Takes seconds. */
export function ago(seconds) {
  if (!isNum(seconds) || seconds < 0) return DASH
  if (seconds < 90) return `${Math.round(seconds)}s ago`
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

/**
 * When the next run is due, named rather than counted.
 *
 * "in 2d" was the old answer and it was both vague and, rounding 38 hours up,
 * arguably wrong. The exact moment is known — it comes from the schedule — so
 * the honest thing is to say it: "Mon 17:00". Inside a day a countdown still
 * reads better, so that is kept for the near cases only.
 */
export function until(iso) {
  if (!iso) return DASH
  const target = new Date(iso)
  if (Number.isNaN(target.getTime())) return DASH

  const delta = (target.getTime() - Date.now()) / 1000
  if (delta <= 0) return 'due now'

  if (delta < 3600) return `in ${Math.max(1, Math.round(delta / 60))}m`
  if (delta < 8 * 3600) {
    const hours = Math.floor(delta / 3600)
    const minutes = Math.round((delta % 3600) / 60)
    return minutes ? `in ${hours}h ${minutes}m` : `in ${hours}h`
  }

  const when = target.toLocaleString('en-GB', {
    weekday: 'short', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'Asia/Karachi',
  })
  const sameDay =
    target.toLocaleDateString('en-GB', { timeZone: 'Asia/Karachi' }) ===
    new Date().toLocaleDateString('en-GB', { timeZone: 'Asia/Karachi' })
  return sameDay ? when.split(', ').pop() : when.replace(',', '')
}

/** Title-case a SHOUTED sector name without mangling short words. */
export function titleCase(value) {
  if (!value) return DASH
  return value
    .toLowerCase()
    .replace(/\b([a-z])/g, (m) => m.toUpperCase())
    .replace(/\band\b/gi, 'and')
    .replace(/\bOf\b/g, 'of')
    .replace(/\b&\b/g, '&')
}
