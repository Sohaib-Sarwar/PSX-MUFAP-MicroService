import { useMemo, useState } from 'react'
import {
  FiActivity,
  FiBarChart2,
  FiFilter,
  FiLayers,
  FiTrendingDown,
  FiTrendingUp,
} from 'react-icons/fi'
import DataTable from '../components/DataTable'
import Freshness from '../components/Freshness'
import { Badge, Card, Failed, Loading, Search, Segmented, Select, Stat } from '../components/ui'
import { useData, useDebounced, usePage, useSort } from '../lib/hooks'
import { DASH, compact, direction, int, num, pct, pkr, titleCase } from '../lib/format'

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
        <div className="cell-primary">
          <span className="sym">{row.symbol}</span>
          {!row.traded && <Badge>not traded</Badge>}
          {row.is_etf && <Badge variant="brand">ETF</Badge>}
          {row.is_debt && <Badge variant="accent">debt</Badge>}
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
          <span className="pct">
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
    render: (row) => <span className="num">{compact(row.market_cap)}</span>,
  },
  {
    key: 'volume_30d_avg',
    header: '30d avg vol',
    align: 'right',
    hide: 'sm',
    render: (row) => <span className="num">{compact(row.volume_30d_avg)}</span>,
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
        <span className="num">{compact(row.volume)}</span>
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
      <div className="grid grid--stats">
        <Stat icon={FiLayers} label="Listed" value={int(breadth.listed_instruments)} tone="brand" />
        <Stat
          icon={FiBarChart2}
          label="Traded"
          value={int(breadth.traded_instruments)}
          note="in the session"
          tone="brand"
        />
        <Stat icon={FiTrendingUp} label="Advancing" value={int(breadth.gainers)} tone="up" />
        <Stat icon={FiTrendingDown} label="Declining" value={int(breadth.losers)} tone="down" />
        <Stat
          icon={FiActivity}
          label="Volume"
          value={compact(breadth.total_volume)}
          note={pkr(breadth.total_traded_value)}
          tone="accent"
        />
        <Stat
          icon={FiTrendingUp}
          label="Average move"
          value={pct(breadth.avg_change_pct)}
          tone={direction(breadth.avg_change_pct)}
        />
      </div>

      <Freshness freshness={state.bodies[0].freshness} label="PSX" />

      <Card
        icon={FiBarChart2}
        title="Instruments"
        subtitle={`${filtered.length.toLocaleString()} matching`}
        flush
        actions={
          <div className="toolbar">
            <Segmented options={VIEWS} value={view} onChange={changeView} label="Stock view" />
          </div>
        }
      >
        <div className="card-body" style={{ paddingBottom: 0 }}>
          <div className="toolbar">
            <div className="grow">
              <Search
                value={query}
                onChange={setQuery}
                placeholder="Symbol or company name…"
                label="Search instruments"
              />
            </div>
            <Select
              icon={FiFilter}
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
      </Card>
    </>
  )
}
