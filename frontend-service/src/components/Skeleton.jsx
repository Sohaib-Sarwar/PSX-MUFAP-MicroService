export function CardSkeleton({ count = 4 }) {
  return (
    <div className="skeleton-list">
      {Array.from({ length: count }).map((_, i) => (
        <div className="skeleton-card" key={i}>
          <div className="sk-line sk-w60" />
          <div className="sk-line sk-w40" />
          <div className="sk-line sk-w80" />
        </div>
      ))}
    </div>
  )
}

export function StatSkeleton({ count = 3 }) {
  return (
    <div className="stats-row">
      {Array.from({ length: count }).map((_, i) => (
        <div className="stat-card" key={i}>
          <div className="sk-line sk-w60 sk-sm" />
          <div className="sk-line sk-w40 sk-lg" />
        </div>
      ))}
    </div>
  )
}

export function Spinner() {
  return (
    <svg className="spinner-svg" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeDasharray="32 32" />
    </svg>
  )
}

export function EmptyState({ icon: Icon, title, sub }) {
  return (
    <div className="empty-state">
      {Icon && <Icon sx={{ fontSize: 40, color: 'var(--text-tertiary)' }} />}
      <p className="empty-title">{title}</p>
      {sub && <p className="empty-sub">{sub}</p>}
    </div>
  )
}
