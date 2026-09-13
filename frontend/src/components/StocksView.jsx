import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import Freshness from './Freshness'
import { Loading, Empty, Failed } from './States'

const VIEWS = [
  { id: 'active', label: 'Most active' },
  { id: 'gainers', label: 'Gainers' },
  { id: 'losers', label: 'Losers' },
  { id: 'all', label: 'All listed' },
]

const price = (v) => (v == null ? '—' : Number(v).toFixed(2))
const pct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`)

function volume(v) {
  if (!v) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return v.toLocaleString()
}

export default function StocksView({ nonce }) {
  const [view, setView] = useState('active')
  const [query, setQuery] = useState('')
  const [term, setTerm] = useState('')
  const [state, setState] = useState({ status: 'loading' })
  const [summary, setSummary] = useState(null)

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })

    const load = term
      ? api.searchStocks(term)
      : view === 'gainers' ? api.gainers(50)
      : view === 'losers' ? api.losers(50)
      : view === 'active' ? api.active(50)
      : api.stocks({ limit: 250, sort_by: 'volume', ascending: false })

    Promise.all([load, api.summary().catch(() => null)])
      .then(([body, sum]) => {
        if (cancelled) return
        setState({ status: 'ready', body })
        if (sum) setSummary(sum)
      })
      .catch((error) => !cancelled && setState({ status: 'error', error }))

    return () => { cancelled = true }
  }, [view, term, nonce])

  const rows = useMemo(() => state.body?.data || [], [state.body])

  return (
    <section className="view">
      {summary && (
        <div className="stats">
          <div className="stat">
            <span className="stat-k">Listed</span>
            <span className="stat-v">{summary.listed_instruments?.toLocaleString()}</span>
          </div>
          <div className="stat">
            <span className="stat-k">Traded</span>
            <span className="stat-v">{summary.traded_instruments?.toLocaleString()}</span>
          </div>
          <div className="stat">
            <span className="stat-k">Advancing</span>
            <span className="stat-v up">{summary.gainers}</span>
          </div>
          <div className="stat">
            <span className="stat-k">Declining</span>
            <span className="stat-v down">{summary.losers}</span>
          </div>
          <div className="stat">
            <span className="stat-k">Volume</span>
            <span className="stat-v">{volume(summary.total_volume)}</span>
          </div>
        </div>
      )}

      <div className="toolbar">
        <div className="segmented" role="tablist">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              role="tab"
              aria-selected={!term && view === v.id}
              className={`seg${!term && view === v.id ? ' is-active' : ''}`}
              onClick={() => { setTerm(''); setQuery(''); setView(v.id) }}
            >
              {v.label}
            </button>
          ))}
        </div>
        <form
          className="search"
          onSubmit={(e) => { e.preventDefault(); setTerm(query.trim()) }}
        >
          <input
            id="stock-search"
            type="search"
            value={query}
            placeholder="Symbol or company…"
            onChange={(e) => setQuery(e.target.value)}
          />
          {term && (
            <button type="button" className="ghost" onClick={() => { setTerm(''); setQuery('') }}>
              Clear
            </button>
          )}
        </form>
      </div>

      {state.status === 'loading' && <Loading rows={8} />}
      {state.status === 'error' && <Failed error={state.error} />}

      {state.status === 'ready' && (
        <>
          <Freshness freshness={state.body.freshness} />
          {!rows.length ? (
            <Empty title="No matching instruments" hint="Try a different symbol." />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th className="hide-sm">Company</th>
                    <th className="right">Price</th>
                    <th className="right">Change</th>
                    <th className="right hide-sm">Volume</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => {
                    const dir = s.change > 0 ? 'up' : s.change < 0 ? 'down' : 'flat'
                    return (
                      <tr key={s.symbol}>
                        <td>
                          <span className="sym">{s.symbol}</span>
                          {!s.traded && <span className="tag">not traded</span>}
                          {s.is_etf && <span className="tag">ETF</span>}
                          {s.is_debt && <span className="tag">debt</span>}
                        </td>
                        <td className="hide-sm muted">
                          {s.name || '—'}
                          {s.sector && <span className="sector">{s.sector}</span>}
                        </td>
                        <td className="right num">{price(s.current)}</td>
                        <td className={`right num change--${dir}`}>
                          {s.change == null ? '—' : `${s.change > 0 ? '+' : ''}${price(s.change)}`}
                          <span className="pct">{pct(s.change_pct)}</span>
                        </td>
                        <td className="right num hide-sm">{volume(s.volume)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}
