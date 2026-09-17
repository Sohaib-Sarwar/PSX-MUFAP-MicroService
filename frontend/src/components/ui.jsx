import { useEffect, useState } from 'react'
import {
  FiAlertTriangle,
  FiCheck,
  FiChevronDown,
  FiCopy,
  FiInbox,
  FiSearch,
  FiWifiOff,
  FiX,
} from 'react-icons/fi'

/* ── layout primitives ─────────────────────────────────────────────────── */

export function Card({ icon: Icon, title, subtitle, actions, flush, children, ...rest }) {
  return (
    <section className="card" {...rest}>
      {(title || actions) && (
        <header className="card-head">
          {Icon && (
            <span className="card-icon" aria-hidden="true">
              <Icon />
            </span>
          )}
          <div>
            <h2>{title}</h2>
            {subtitle && <p className="card-sub">{subtitle}</p>}
          </div>
          {actions && <div className="right-slot">{actions}</div>}
        </header>
      )}
      <div className={flush ? 'card-body card-body--flush' : 'card-body'}>{children}</div>
    </section>
  )
}

export function Stat({ icon: Icon, label, value, note, tone }) {
  const style = tone ? { '--tone': `var(--${tone})` } : undefined
  return (
    <div className="stat" style={style}>
      <p className="stat-k">
        {Icon && <Icon aria-hidden="true" />}
        {label}
      </p>
      <p className="stat-v">{value}</p>
      {note && <p className="stat-note">{note}</p>}
    </div>
  )
}

export function Badge({ variant, icon: Icon, children }) {
  return (
    <span className={variant ? `badge badge--${variant}` : 'badge'}>
      {Icon && <Icon aria-hidden="true" />}
      {children}
    </span>
  )
}

/* ── controls ──────────────────────────────────────────────────────────── */

export function Segmented({ options, value, onChange, label }) {
  return (
    <div className="segmented" role="tablist" aria-label={label}>
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

export function Search({ value, onChange, placeholder = 'Search…', label }) {
  return (
    <div className="field">
      <FiSearch aria-hidden="true" />
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
          aria-label="Clear search"
          onClick={() => onChange('')}
        >
          <FiX size={12} />
        </button>
      )}
    </div>
  )
}

export function Select({ value, onChange, options, label, icon: Icon }) {
  return (
    <div className={Icon ? 'field' : 'field field--plain'}>
      {Icon && <Icon aria-hidden="true" />}
      <select value={value} aria-label={label} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <FiChevronDown className="field-caret" aria-hidden="true" />
    </div>
  )
}

export function Copy({ text, label = 'Copy', className = 'btn btn--sm' }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return undefined
    const timer = setTimeout(() => setCopied(false), 1600)
    return () => clearTimeout(timer)
  }, [copied])

  const write = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      // Clipboard access can be refused outright (insecure context, permission
      // policy). Selecting the text by hand still works, so this stays silent.
    }
  }

  return (
    <button type="button" className={className} onClick={write} aria-label={label}>
      {copied ? <FiCheck aria-hidden="true" /> : <FiCopy aria-hidden="true" />}
      <span className="hide-sm">{copied ? 'Copied' : label}</span>
    </button>
  )
}

export function CodeBlock({ code, copy = true }) {
  return (
    <div className="code-block">
      <pre className="code">{code}</pre>
      {copy && <Copy text={code} className="btn btn--sm copy" />}
    </div>
  )
}

export function Callout({ icon: Icon = FiAlertTriangle, variant, children }) {
  return (
    <div className={variant ? `callout callout--${variant}` : 'callout'}>
      <Icon aria-hidden="true" />
      <div>{children}</div>
    </div>
  )
}

/* ── states ────────────────────────────────────────────────────────────── */

export function Loading({ rows = 6 }) {
  return (
    <div className="skeleton-stack" role="status" aria-live="polite">
      <span className="visually-hidden">Loading</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div className="skeleton" key={index} style={{ opacity: 1 - index * 0.09 }} />
      ))}
    </div>
  )
}

export function Empty({ title = 'Nothing to show', hint, icon: Icon = FiInbox }) {
  return (
    <div className="notice">
      <span className="notice-icon">
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
    <div className="notice notice--error">
      <span className="notice-icon">
        {offline ? <FiWifiOff aria-hidden="true" /> : <FiAlertTriangle aria-hidden="true" />}
      </span>
      <h3>Could not load this data</h3>
      <p>{error?.message || 'Something went wrong.'}</p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
