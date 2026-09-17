import { useMemo, useState } from 'react'
import {
  FiArrowDownRight,
  FiArrowUpRight,
  FiGrid,
  FiList,
  FiMinus,
  FiTrendingDown,
  FiTrendingUp,
} from 'react-icons/fi'
import Freshness from '../components/Freshness'
import { Card, Empty, Failed, Loading, Search, Segmented, Stat } from '../components/ui'
import { useData, useDebounced } from '../lib/hooks'
import { direction, int, num, pct } from '../lib/format'

const LAYOUTS = [
  { id: 'cards', label: 'Cards' },
  { id: 'table', label: 'Table' },
]

export default function Indices({ nonce }) {
  const state = useData(['indices'], nonce)
  const [layout, setLayout] = useState('cards')
  const [query, setQuery] = useState('')
  const term = useDebounced(query)

  const body = state.status === 'ready' ? state.bodies[0] : null
  const all = body?.data || []

  const rows = useMemo(() => {
    const needle = term.trim().toLowerCase()
    const filtered = needle
      ? all.filter((row) => (row.index_name || '').toLowerCase().includes(needle))
      : all
    // Advancing first, then by size of move: the board reads as a ranking
    // rather than as whatever order PSX happened to render it in.
    return [...filtered].sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0))
  }, [all, term])

  const breadth = useMemo(() => {
    const up = all.filter((row) => (row.change ?? 0) > 0).length
    const down = all.filter((row) => (row.change ?? 0) < 0).length
    return { up, down, flat: all.length - up - down }
  }, [all])

  if (state.status === 'loading') return <Loading rows={7} />
  if (state.status === 'error') return <Failed error={state.error} />

  return (
    <>
      <div className="grid grid--stats">
        <Stat icon={FiList} label="Indices" value={int(all.length)} tone="brand" />
        <Stat icon={FiTrendingUp} label="Advancing" value={int(breadth.up)} tone="up" />
        <Stat icon={FiTrendingDown} label="Declining" value={int(breadth.down)} tone="down" />
        <Stat icon={FiMinus} label="Unchanged" value={int(breadth.flat)} tone="flat" />
      </div>

      <Freshness freshness={body.freshness} label="PSX" />

      <Card
        icon={FiGrid}
        title="Index board"
        subtitle="Ranked by percentage move at the close"
        flush={layout === 'table'}
        actions={
          <>
            <Search value={query} onChange={setQuery} placeholder="Index…" label="Search indices" />
            <Segmented options={LAYOUTS} value={layout} onChange={setLayout} label="Layout" />
          </>
        }
      >
        {!rows.length && <Empty title="No indices match" hint="Clear the search to see the board." />}

        {rows.length > 0 && layout === 'cards' && (
          <div className="grid grid--cards">
            {rows.map((index) => {
              const dir = direction(index.change)
              const Trend =
                dir === 'up' ? FiArrowUpRight : dir === 'down' ? FiArrowDownRight : FiMinus
              return (
                <article
                  className="quote"
                  key={index.index_name}
                  style={{ '--tone': `var(--${dir})`, '--tone-soft': `var(--${dir}-soft)` }}
                >
                  <div className="quote-top">
                    <span className="quote-name">{index.index_name}</span>
                    <span className="quote-trend" aria-hidden="true">
                      <Trend />
                    </span>
                  </div>
                  <p className="quote-value">{num(index.current ?? index.value)}</p>
                  <p className="quote-change">
                    {index.change > 0 ? '+' : ''}
                    {num(index.change)} · {pct(index.change_pct)}
                  </p>
                  {index.high != null && (
                    <p className="quote-meta">
                      <span>H {num(index.high)}</span>
                      <span>L {num(index.low)}</span>
                    </p>
                  )}
                </article>
              )
            })}
          </div>
        )}

        {rows.length > 0 && layout === 'table' && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Index</th>
                  <th scope="col" className="right">Level</th>
                  <th scope="col" className="right">Change</th>
                  <th scope="col" className="right hide-sm">High</th>
                  <th scope="col" className="right hide-sm">Low</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((index) => {
                  const dir = direction(index.change)
                  return (
                    <tr key={index.index_name}>
                      <td>
                        <span className="sym">{index.index_name}</span>
                      </td>
                      <td className="right num">{num(index.current ?? index.value)}</td>
                      <td className={`right delta delta--${dir}`}>
                        {pct(index.change_pct)}
                        <span className="pct">
                          {index.change > 0 ? '+' : ''}
                          {num(index.change)}
                        </span>
                      </td>
                      <td className="right num hide-sm">{num(index.high)}</td>
                      <td className="right num hide-sm">{num(index.low)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
