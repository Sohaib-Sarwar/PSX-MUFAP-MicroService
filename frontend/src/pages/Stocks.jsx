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
import { compact, direction, int, num, pct, pkr, titleCase } from '../lib/format'

const VIEWS = [
  { id: 'active', label: 'Most active' },
  { id: 'gainers', label: 'Gainers' },
  { id: 'losers', label: 'Losers' },
  { id: 'all', label: 'All listed' },
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
  { key: 'volume', header: 'Volume', align: 'right', render: (row) => <span className="num">{compact(row.volume)}</span> },
  {
    key: 'open',
    header: 'Open',
    align: 'right',
    hide: 'sm',
    render: (row) => <span className="num">{num(row.open)}</span>,
  },
  {
    key: 'high',
    header: 'High / Low',
    align: 'right',
    hide: 'md',
    render: (row) => (
      <span className="num">
        {num(row.high)}
        <span className="pct faint">{num(row.low)}</span>
      </span>
    ),
  },
  {
    key: 'ldcp',
    header: 'LDCP',
    align: 'right',
    hide: 'md',
    render: (row) => <span className="num">{num(row.ldcp)}</span>,
  },
]

export default function Stocks({ nonce }) {
  const state = useData(['stocks', 'summary'], nonce)
  const [view, setView] = useState('active')
  const [sector, setSector] = useState('')
  const [query, setQuery] = useState('')
  const term = useDebounced(query)

  const { sort, toggle, apply, set } = useSort('volume')

  // Switching view re-points the sort at the column that view is about — a
  // gainers list ordered by volume is not a gainers list.
  const changeView = (next) => {
    setView(next)
    if (next === 'gainers') set({ key: 'change_pct', ascending: false })
    else if (next === 'losers') set({ key: 'change_pct', ascending: true })
    else set({ key: 'volume', ascending: false })
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

    if (view !== 'all') rows = rows.filter((row) => row.traded)
    if (view === 'gainers') rows = rows.filter((row) => (row.change_pct ?? 0) > 0)
    if (view === 'losers') rows = rows.filter((row) => (row.change_pct ?? 0) < 0)
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
