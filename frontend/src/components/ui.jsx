import { useEffect, useState } from 'react'
import {
  IconCaretDown,
  IconCheck,
  IconCopy,
  IconEmpty,
  IconOffline,
  IconSearch,
  IconWarn,
} from '../lib/icons'

/* ── surfaces ──────────────────────────────────────────────────────────── */

export function Panel({ icon: Icon, title, sub, end, flush, children, ...rest }) {
  return (
    <section className="panel" {...rest}>
      {(title || end) && (
        <header className="panel-head">
          {Icon && <Icon className="lead" aria-hidden="true" />}
          <div>
            <h2>{title}</h2>
            {sub && <p className="panel-sub">{sub}</p>}
          </div>
          {end && <div className="end">{end}</div>}
        </header>
      )}
      <div className={flush ? 'panel-body panel-body--flush' : 'panel-body'}>{children}</div>
    </section>
  )
}

/**
 * One figure.
 *
 * `value` is expected to already fit on a line — the tile clips rather than
 * wraps, because a KPI that reflows to two lines stops being scannable and
 * drags every neighbouring tile taller with it.
 */
export function Kpi({ icon: Icon, label, value, note, tone }) {
  return (
    <div className="kpi" style={tone ? { '--tone': `var(--${tone})` } : undefined}>
      <p className="kpi-k">
        {Icon && <Icon aria-hidden="true" />}
        {label}
      </p>
      <p className="kpi-v" title={typeof value === 'string' ? value : undefined}>
        {value}
      </p>
      {note && <p className="kpi-note" title={note}>{note}</p>}
    </div>
  )
}

export function Tag({ variant, icon: Icon, children }) {
  return (
    <span className={variant ? `tag tag--${variant}` : 'tag'}>
      {Icon && <Icon aria-hidden="true" />}
      {children}
    </span>
  )
}

export function Note({ icon: Icon = IconWarn, variant, children }) {
  return (
    <div className={variant ? `note note--${variant}` : 'note'}>
      <Icon aria-hidden="true" />
      <div>{children}</div>
    </div>
  )
}

/* ── controls ──────────────────────────────────────────────────────────── */

export function Segmented({ options, value, onChange, label }) {
  return (
    <div className="seg-group" role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          role="tab"
          aria-selected={value === option.id}
          className={`seg${value === option.id ? ' is-active' : ''}`}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function Search({ value, onChange, placeholder = 'Search', label }) {
  return (
    <div className="field">
      <IconSearch aria-hidden="true" />
      <input
        type="search"
        value={value}
        aria-label={label || placeholder}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {value && (
        <button
          type="button"
          className="field-clear"
          aria-label="Clear"
          onClick={() => onChange('')}
        >
          ×
        </button>
      )}
    </div>
  )
}

export function Select({ value, onChange, options, label, icon: Icon }) {
  return (
    <div className="field">
      {Icon && <Icon aria-hidden="true" />}
      <select
        value={value}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
        style={Icon ? undefined : { paddingLeft: 10 }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <IconCaretDown className="field-caret" aria-hidden="true" />
    </div>
  )
}

export function Copy({ text, label = 'Copy', className = 'btn btn--sm' }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return undefined
    const timer = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(timer)
  }, [copied])

  return (
    <button
      type="button"
      className={className}
      aria-label={label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
        } catch {
          // Clipboard access can be refused outright. Selecting by hand still
          // works, so this stays silent rather than raising an alarm.
        }
      }}
    >
      {copied ? <IconCheck aria-hidden="true" /> : <IconCopy aria-hidden="true" />}
      <span className="hide-sm">{copied ? 'Copied' : label}</span>
    </button>
  )
}

export function Code({ code, copy = true }) {
  return (
    <div className="code-wrap">
      <pre className="code">{code}</pre>
      {copy && <Copy text={code} className="btn btn--sm copy" />}
    </div>
  )
}

/* ── states ────────────────────────────────────────────────────────────── */

export function Loading({ rows = 5 }) {
  return (
    <div className="skeletons" role="status" aria-live="polite">
      <span className="visually-hidden">Loading</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div className="skeleton" key={index} style={{ opacity: 1 - index * 0.13 }} />
      ))}
    </div>
  )
}

export function Empty({ title = 'Nothing here', hint, icon: Icon = IconEmpty }) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon aria-hidden="true" />
      </span>
      <h3>{title}</h3>
      {hint && <p>{hint}</p>}
    </div>
  )
}

export function Failed({ error, onRetry }) {
  const offline = error?.status === 0
  return (
    <div className="empty empty--bad">
      <span className="empty-icon">
        {offline ? <IconOffline aria-hidden="true" /> : <IconWarn aria-hidden="true" />}
      </span>
      <h3>Could not load this</h3>
      <p>{error?.message || 'Something went wrong.'}</p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
