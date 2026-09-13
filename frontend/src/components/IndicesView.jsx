import { useEffect, useState } from 'react'
import { api } from '../api'
import Freshness from './Freshness'
import { Loading, Empty, Failed } from './States'

const num = (v, dp = 2) =>
  v == null ? '—' : Number(v).toLocaleString(undefined, {
    minimumFractionDigits: dp, maximumFractionDigits: dp,
  })

export default function IndicesView({ nonce }) {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })
    api.indices()
      .then((body) => !cancelled && setState({ status: 'ready', body }))
      .catch((error) => !cancelled && setState({ status: 'error', error }))
    return () => { cancelled = true }
  }, [nonce])

  if (state.status === 'loading') return <Loading rows={6} />
  if (state.status === 'error') return <Failed error={state.error} />

  const rows = state.body.data || []
  if (!rows.length) return <Empty title="No index data" />

  return (
    <section className="view">
      <Freshness freshness={state.body.freshness} />
      <div className="grid">
        {rows.map((idx) => {
          // The API returns index_name; `name` is an alias it also sets. The
          // old dashboard read only `name`/`index` and rendered a dash for all.
          const label = idx.index_name || idx.name
          const dir = idx.change > 0 ? 'up' : idx.change < 0 ? 'down' : 'flat'
          return (
            <article className={`card card--${dir}`} key={label}>
              <h3 className="card-title">{label}</h3>
              <p className="card-value">{num(idx.current ?? idx.value)}</p>
              <p className={`card-change change--${dir}`}>
                {idx.change > 0 ? '+' : ''}{num(idx.change)}
                <span className="pct">
                  ({idx.change_pct > 0 ? '+' : ''}{num(idx.change_pct)}%)
                </span>
              </p>
              {idx.high != null && (
                <p className="card-meta">H {num(idx.high)} · L {num(idx.low)}</p>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
