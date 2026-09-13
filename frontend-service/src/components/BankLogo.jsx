import { useState } from 'react'

/*
 * Maps fund-name prefixes AND stock symbols to local SVG/PNG files
 * in /Bank Logos/ folder.
 *
 * Available logos:
 *   Allied Bank.svg, Askari_Logo.svg, HBL.svg, MCB.svg,
 *   Meezan Bank.svg, UBL.svg
 */

/* ── Fund name prefix → logo file ── */
const FUND_PREFIX_MAP = [
  // Longest prefixes first so "Meezan Bank" matches before "Meezan"
  { prefix: 'Al Meezan',      logo: 'Meezan Bank.svg' },
  { prefix: 'Meezan',         logo: 'Meezan Bank.svg' },
  { prefix: 'Allied',         logo: 'Allied Bank.svg' },
  { prefix: 'ABL',            logo: 'Allied Bank.svg' },
  { prefix: 'Askari',         logo: 'Askari_Logo.svg' },
  { prefix: 'HBL',            logo: 'HBL.svg' },
  { prefix: 'Habib',          logo: 'HBL.svg' },
  { prefix: 'MCB',            logo: 'MCB.svg' },
  { prefix: 'UBL',            logo: 'UBL.svg' },
  { prefix: 'United',         logo: 'UBL.svg' },
]

/* ── Stock symbol → logo file ── */
const STOCK_SYMBOL_MAP = {
  'HBL':   'HBL.svg',
  'UBL':   'UBL.svg',
  'MCB':   'MCB.svg',
  'MEBL':  'Meezan Bank.svg',
  'ABL':   'Allied Bank.svg',
  'AKBL':  'Askari_Logo.svg',
}

/* ── Color palette for fallback avatars ── */
const COLORS = [
  '#1a73e8', '#e8453c', '#0d9d58', '#f4b400', '#ab47bc',
  '#00897b', '#e65100', '#5c6bc0', '#c62828', '#2e7d32',
  '#00838f', '#6d4c41', '#546e7a', '#d81b60', '#1565c0',
  '#ff6f00', '#283593', '#00695c', '#ad1457', '#4527a0',
]

function hashColor(str) {
  let h = 0
  for (let i = 0; i < str.length; i++) h = ((h << 5) - h + str.charCodeAt(i)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

function getInitials(name) {
  if (!name) return '?'
  const words = name.trim().split(/\s+/)
  if (words.length === 1) return words[0].substring(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

/* ── Resolve logo path ── */
function resolveLogo(name, symbol, type) {
  if (type === 'stock' && symbol && STOCK_SYMBOL_MAP[symbol]) {
    return `/Bank%20Logos/${STOCK_SYMBOL_MAP[symbol]}`
  }
  if (type === 'fund' && name) {
    const lower = name.toLowerCase()
    for (const entry of FUND_PREFIX_MAP) {
      if (lower.startsWith(entry.prefix.toLowerCase())) {
        return `/Bank%20Logos/${entry.logo}`
      }
    }
  }
  return null
}

/* ── BankLogo Component ── */
export default function BankLogo({ name, symbol, size = 32, type = 'fund' }) {
  const [failed, setFailed] = useState(false)

  const logoPath = resolveLogo(name, symbol, type)
  const label = type === 'stock' ? (symbol || '?') : (name || '?')
  const initials = type === 'stock'
    ? (symbol || '?').substring(0, 2)
    : getInitials(name)
  const bg = hashColor(label)

  if (!logoPath || failed) {
    return (
      <div
        className="bank-avatar"
        style={{ width: size, height: size, background: bg, fontSize: size * 0.36 }}
        title={label}
      >
        {initials}
      </div>
    )
  }

  return (
    <img
      className="bank-logo-img"
      src={logoPath}
      alt={label}
      width={size}
      height={size}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}
