export function Loading({ rows = 6 }) {
  return (
    <div className="skeletons">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skeleton" key={i} />
      ))}
    </div>
  )
}

export function Empty({ title, hint }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {hint && <p className="empty-hint">{hint}</p>}
    </div>
  )
}

export function Failed({ error, onRetry }) {
  return (
    <div className="empty empty--error">
      <p className="empty-title">Could not load data</p>
      <p className="empty-hint">{error?.message || 'Unknown error'}</p>
      {onRetry && (
        <button className="ghost" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
