import { useMemo, useState } from 'react'
import {
  IconFilter,
  IconPulse,
  IconRise,
  IconStocks,
  IconUp,
} from '../lib/icons'
import DataTable from '../components/DataTable'
import Freshness from '../components/Freshness'
import { Failed, Kpi, Loading, Panel, Search, Segmented, Select, Tag } from '../components/ui'
import { useData, useDebounced, usePage, useSort } from '../lib/hooks'
import { DASH, compact, compactOrDash, direction, int, num, pct, pkr, titleCase } from '../lib/format'

const VIEWS = [
  { id: 'all', label: 'All listed' },
  { id: 'gainers', label: 'Gainers' },
  { id: 'losers', label: 'Losers' },
  { id: 'active', label: 'Most traded' },
  { id: 'quoted', label: 'Full quote' },
]

const COLUMNS = [
  {
    key: 'symbol',
    header: 'Symbol',
    render: (row) => (
      <>
        <div className="cell-top">
          <span className="sym">{row.symbol}</span>
          {!row.traded && <Tag>not traded</Tag>}
          {row.is_etf && <Tag variant="brand">ETF</Tag>}
          {row.is_debt && <Tag variant="accent">debt</Tag>}
        </div>
        <span className="cell-sub">{row.name || '—'}</span>
      </>
    ),
  },
  {
    key: 'sector',
    header: 'Sector',
    hide: 'md',
    render: (row) => <span className="muted">{titleCase(row.sector)}</span>,
  },
  { key: 'current', header: 'Price', align: 'right', render: (row) => <span className="num">{num(row.current)}</span> },
  {
    key: 'change_pct',
    header: 'Change',
    align: 'right',
    render: (row) => {
      const dir = direction(row.change_pct)
      return (
        <span className={`delta delta--${dir}`}>
          {pct(row.change_pct)}
          <span className="sub">
            {row.change > 0 ? '+' : ''}
            {num(row.change)}
          </span>
        </span>
      )
    },
  },
  {
    key: 'market_cap',
    header: 'Market cap',
    align: 'right',
    render: (row) => <span className="num">{compactOrDash(row.market_cap)}</span>,
  },
  {
    key: 'volume_30d_avg',
    header: '30d avg vol',
    align: 'right',
    hide: 'sm',
    render: (row) => <span className="num">{compactOrDash(row.volume_30d_avg)}</span>,
  },
  {
    key: 'pe_ratio',
    header: 'P/E',
    align: 'right',
    hide: 'md',
    render: (row) => (
      <span className="num">{row.pe_ratio > 0 ? num(row.pe_ratio) : DASH}</span>
    ),
  },
  {
    key: 'dividend_yield',
    header: 'Div yield',
    align: 'right',
    hide: 'md',
    render: (row) => (
      <span className="num">{row.dividend_yield ? `${num(row.dividend_yield)}%` : DASH}</span>
    ),
  },
  {
    key: 'change_1y_pct',
    header: '1 year',
    align: 'right',
    hide: 'md',
    render: (row) => (
      <span className={`delta delta--${direction(row.change_1y_pct)}`}>
        {pct(row.change_1y_pct)}
      </span>
    ),
  },
  {
    key: 'volume',
    header: 'Session vol',
    align: 'right',
    hide: 'md',
    // Only present for the instruments today's bounded quote pass covered.
    render: (row) =>
      row.has_quote ? (
        <span className="num">{compactOrDash(row.volume)}</span>
      ) : (
        <span className="num faint" title="PSX no longer publishes session volume in bulk">
          {DASH}
        </span>
      ),
  },
]

export default function Stocks({ nonce }) {
  const state = useData(['stocks', 'summary'], nonce)
  const [view, setView] = useState('active')
  const [sector, setSector] = useState('')
  const [query, setQuery] = useState('')
  const term = useDebounced(query)

  const { sort, toggle, apply, set } = useSort('market_cap')

  // Switching view re-points the sort at the column that view is about — a
  // gainers list ordered by market cap is not a gainers list.
  const changeView = (next) => {
    setView(next)
    if (next === 'gainers') set({ key: 'change_pct', ascending: false })
    else if (next === 'losers') set({ key: 'change_pct', ascending: true })
    else if (next === 'active') set({ key: 'volume_30d_avg', ascending: false })
    else set({ key: 'market_cap', ascending: false })
  }

  const all = state.status === 'ready' ? state.bodies[0].data || [] : []
  const summary = state.status === 'ready' ? state.bodies[1] : {}

  const sectors = useMemo(() => {
    const names = new Set()
    all.forEach((row) => row.sector && names.add(row.sector))
    return [
      { value: '', label: 'All sectors' },
      ...[...names].sort().map((name) => ({ value: name, label: titleCase(name) })),
    ]
  }, [all])

  const filtered = useMemo(() => {
    const needle = term.trim().toLowerCase()
    let rows = all

    if (view === 'gainers') rows = rows.filter((row) => (row.change_pct ?? 0) > 0)
    if (view === 'losers') rows = rows.filter((row) => (row.change_pct ?? 0) < 0)
    if (view === 'quoted') rows = rows.filter((row) => row.has_quote)
    if (sector) rows = rows.filter((row) => row.sector === sector)
    if (needle) {
      rows = rows.filter(
        (row) =>
          row.symbol?.toLowerCase().includes(needle) ||
          row.name?.toLowerCase().includes(needle)
      )
    }
    return apply(rows)
  }, [all, view, sector, term, apply])

  const page = usePage(filtered, 50)

  if (state.status === 'loading') return <Loading rows={9} />
  if (state.status === 'error') return <Failed error={state.error} />

  const breadth = summary || {}

  return (
    <>
      <div className="grid grid--kpi">
        <Kpi icon={IconStocks} label="Traded" value={int(breadth.traded_instruments)} note={`of ${int(breadth.listed_instruments)} listed`} />
        <Kpi icon={IconRise} label="Advancing" value={int(breadth.gainers)} note={`${int(breadth.losers)} declining`} tone="up" />
        <Kpi icon={IconPulse} label="Volume" value={compact(breadth.total_volume)} note={pkr(breadth.total_traded_value)} />
        <Kpi
          icon={IconUp}
          label="Average move"
          value={pct(breadth.avg_change_pct_quoted)}
          note={`across ${int(breadth.instruments_with_full_quote)} quoted`}
          tone={direction(breadth.avg_change_pct_quoted)}
        />
      </div>

      <Freshness freshness={state.bodies[0].freshness} label="PSX" />

      <Panel
        icon={IconStocks}
        title="Instruments"
        sub={`${filtered.length.toLocaleString()} matching`}
        flush
        end={
          <div className="bar-tools">
            <Segmented options={VIEWS} value={view} onChange={changeView} label="Stock view" />
          </div>
        }
      >
        <div className="panel-body" style={{ paddingBottom: 0 }}>
          <div className="bar-tools">
            <div className="grow">
              <Search
                value={query}
                onChange={setQuery}
                placeholder="Symbol or company name…"
                label="Search instruments"
              />
            </div>
            <Select
              icon={IconFilter}
              value={sector}
              onChange={setSector}
              options={sectors}
              label="Filter by sector"
            />
          </div>
        </div>

        <DataTable
          columns={COLUMNS}
          rows={page.slice}
          rowKey={(row) => row.symbol}
          sort={sort}
          onSort={toggle}
          page={page}
          empty={{
            title: 'No instruments match',
            hint: 'Clear the search or pick a different sector.',
          }}
        />
      </Panel>
    </>
  )
}
