import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import Freshness from './Freshness'
import { Loading, Empty, Failed } from './States'

const PERIODS = [
  { id: 'ytd', label: 'YTD' },
  { id: 'mtd', label: 'MTD' },
  { id: 'd30', label: '30D' },
  { id: 'd365', label: '1Y' },
  { id: 'y3', label: '3Y' },
]

const nav = (v) => (v == null ? '—' : Number(v).toFixed(4))
const ret = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`)

export default function FundsView({ nonce }) {
  const [category, setCategory] = useState('')
  const [period, setPeriod] = useState('ytd')
  const [query, setQuery] = useState('')
  const [term, setTerm] = useState('')
  const [categories, setCategories] = useState([])
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    api.categories()
      .then((b) => !cancelled && setCategories(b.data || []))
      .catch(() => !cancelled && setCategories([]))
    return () => { cancelled = true }
  }, [nonce])

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })

    const load = term
      ? api.searchFunds(term)
      : api.funds({
          limit: 250,
          sort_by: `return_${period}`,
          ascending: false,
          category: category || undefined,
        })

    load
      .then((body) => !cancelled && setState({ status: 'ready', body }))
      .catch((error) => !cancelled && setState({ status: 'error', error }))

    return () => { cancelled = true }
  }, [category, period, term, nonce])

  const rows = useMemo(() => state.body?.data || [], [state.body])

  return (
    <section className="view">
      <div className="toolbar">
        <div className="segmented" role="tablist">
          {PERIODS.map((p) => (
            <button
              key={p.id}
              role="tab"
              aria-selected={period === p.id}
              className={`seg${period === p.id ? ' is-active' : ''}`}
              onClick={() => setPeriod(p.id)}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="filters">
          <select
            id="fund-category"
            value={category}
            onChange={(e) => { setTerm(''); setQuery(''); setCategory(e.target.value) }}
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.category} value={c.category}>
                {c.category} ({c.count})
              </option>
            ))}
          </select>

          <form className="search" onSubmit={(e) => { e.preventDefault(); setTerm(query.trim()) }}>
            <input
              id="fund-search"
              type="search"
              value={query}
              placeholder="Fund or AMC…"
              onChange={(e) => setQuery(e.target.value)}
            />
            {term && (
              <button type="button" className="ghost" onClick={() => { setTerm(''); setQuery('') }}>
                Clear
              </button>
            )}
          </form>
        </div>
      </div>

      {state.status === 'loading' && <Loading rows={8} />}
      {state.status === 'error' && <Failed error={state.error} />}

      {state.status === 'ready' && (
        <>
          <Freshness freshness={state.body.freshness} />
          {!rows.length ? (
            <Empty title="No matching funds" hint="Try a different name or category." />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Fund</th>
                    <th className="hide-sm">Category</th>
                    <th className="right">NAV</th>
                    <th className="right">
                      {PERIODS.find((p) => p.id === period)?.label} return
                    </th>
                    <th className="right hide-sm">Offer</th>
                    <th className="hide-sm">Validity</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((f, i) => {
                    const value = f.returns?.[period]
                    const dir = value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
                    return (
                      <tr key={`${f.fund_name}-${f.category}-${i}`}>
                        <td>
                          <span className="sym">{f.fund_name}</span>
                          {f.rating && <span className="tag">{f.rating}</span>}
                          {f.amc && <span className="sector">{f.amc}</span>}
                        </td>
                        <td className="hide-sm muted">
                          {f.category || '—'}
                          {f.return_basis && (
                            <span className="sector">{f.return_basis} return</span>
                          )}
                        </td>
                        <td className="right num">{nav(f.nav)}</td>
                        <td className={`right num change--${dir}`}>{ret(value)}</td>
                        <td className="right num hide-sm">{nav(f.offer_price)}</td>
                        {/* The API field is validity_date. The old dashboard
                            read f.nav_date, which never existed, so every row
                            showed a dash. */}
                        <td className="hide-sm muted num">{f.validity_date || '—'}</td>
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
