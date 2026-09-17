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

/** "4m ago" / "2d ago". Takes seconds. */
export function ago(seconds) {
  if (!isNum(seconds) || seconds < 0) return DASH
  if (seconds < 90) return `${Math.round(seconds)}s ago`
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

/** "in 3h 20m" — for the next scheduled refresh. */
export function until(iso) {
  if (!iso) return DASH
  const delta = (new Date(iso).getTime() - Date.now()) / 1000
  if (Number.isNaN(delta)) return DASH
  if (delta <= 0) return 'due now'
  const hours = Math.floor(delta / 3600)
  const minutes = Math.round((delta % 3600) / 60)
  if (hours >= 24) return `in ${Math.round(hours / 24)}d`
  if (hours) return `in ${hours}h ${minutes}m`
  return `in ${minutes}m`
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
