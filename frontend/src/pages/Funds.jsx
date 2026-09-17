import { useMemo, useState } from 'react'
import {
  FiAward,
  FiBriefcase,
  FiFilter,
  FiHome,
  FiLayers,
  FiTrendingUp,
} from 'react-icons/fi'
import DataTable from '../components/DataTable'
import Freshness from '../components/Freshness'
import { Badge, Card, Failed, Loading, Search, Segmented, Select, Stat } from '../components/ui'
import { useData, useDebounced, usePage, useSort } from '../lib/hooks'
import { day, direction, int, num, pct } from '../lib/format'

// The periods worth a one-tap button. The API publishes eleven; these five are
// the ones an investor actually compares on.
const PERIODS = [
  { id: 'ytd', label: 'YTD' },
  { id: 'mtd', label: 'MTD' },
  { id: 'd30', label: '30D' },
  { id: 'd365', label: '1Y' },
  { id: 'y3', label: '3Y' },
]

export default function Funds({ nonce }) {
  const state = useData(['funds', 'fundStats', 'categories'], nonce)
  const [period, setPeriod] = useState('ytd')
  const [category, setCategory] = useState('')
  const [amc, setAmc] = useState('')
  const [query, setQuery] = useState('')
  const term = useDebounced(query)
  const { sort, toggle, apply, set } = useSort('return', false)

  const body = state.status === 'ready' ? state.bodies[0] : null
  const stats = state.status === 'ready' ? state.bodies[1] : {}
  const all = body?.data || []

  const changePeriod = (next) => {
    setPeriod(next)
    set({ key: 'return', ascending: false })
  }

  const categories = useMemo(() => {
    const rows = state.status === 'ready' ? state.bodies[2].data || [] : []
    return [
      { value: '', label: 'All categories' },
      ...rows.map((row) => ({ value: row.category, label: `${row.category} (${row.count})` })),
    ]
  }, [state])

  const amcs = useMemo(() => {
    const counts = new Map()
    all.forEach((fund) => {
      if (fund.amc) counts.set(fund.amc, (counts.get(fund.amc) || 0) + 1)
    })
    return [
      { value: '', label: 'All AMCs' },
      ...[...counts.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([name, count]) => ({ value: name, label: `${name} (${count})` })),
    ]
  }, [all])

  const filtered = useMemo(() => {
    const needle = term.trim().toLowerCase()
    let rows = all
    if (category) rows = rows.filter((fund) => fund.category === category)
    if (amc) rows = rows.filter((fund) => fund.amc === amc)
    if (needle) {
      rows = rows.filter(
        (fund) =>
          fund.fund_name?.toLowerCase().includes(needle) ||
          fund.amc?.toLowerCase().includes(needle)
      )
    }
    // `return` is not a column on the record — it is whichever period is
    // selected, so the sort reads it through an accessor rather than the table
    // carrying eleven near-identical columns.
    return apply(rows, { return: (fund) => fund.returns?.[period] ?? null })
  }, [all, category, amc, term, apply, period])

  const page = usePage(filtered, 50)

  const periodLabel = PERIODS.find((entry) => entry.id === period)?.label || period.toUpperCase()

  const columns = useMemo(
    () => [
      {
        key: 'fund_name',
        header: 'Fund',
        render: (fund) => (
          <>
            <div className="cell-primary">
              <span className="sym">{fund.fund_name}</span>
              {fund.rating && <Badge variant="brand">{fund.rating}</Badge>}
              {fund.sector?.toLowerCase().includes('voluntary') && <Badge variant="accent">VPS</Badge>}
            </div>
            <span className="cell-sub">{fund.amc || '—'}</span>
          </>
        ),
      },
      {
        key: 'category',
        header: 'Category',
        hide: 'md',
        render: (fund) => (
          <>
            <span className="muted">{fund.category || '—'}</span>
            {fund.return_basis && <span className="cell-sub">{fund.return_basis} returns</span>}
          </>
        ),
      },
      {
        key: 'nav',
        header: 'NAV',
        align: 'right',
        render: (fund) => <span className="num">{num(fund.nav, 4)}</span>,
      },
      {
        key: 'return',
        header: `${periodLabel} return`,
        align: 'right',
        render: (fund) => {
          const value = fund.returns?.[period]
          return <span className={`delta delta--${direction(value)}`}>{pct(value)}</span>
        },
      },
      {
        key: 'offer_price',
        header: 'Offer',
        align: 'right',
        hide: 'sm',
        render: (fund) => <span className="num">{num(fund.offer_price, 4)}</span>,
      },
      {
        key: 'repurchase_price',
        header: 'Repurchase',
        align: 'right',
        hide: 'md',
        render: (fund) => <span className="num">{num(fund.repurchase_price, 4)}</span>,
      },
      {
        key: 'front_end_load',
        header: 'Front load',
        align: 'right',
        hide: 'md',
        render: (fund) => (
          <span className="num">{fund.front_end_load != null ? `${num(fund.front_end_load, 2)}%` : '—'}</span>
        ),
      },
      {
        key: 'validity_date',
        header: 'Valid for',
        hide: 'sm',
        render: (fund) => <span className="num faint">{day(fund.validity_date)}</span>,
      },
    ],
    [period, periodLabel]
  )

  if (state.status === 'loading') return <Loading rows={9} />
  if (state.status === 'error') return <Failed error={state.error} />

  const best = stats.ytd_return || {}
  const nav = stats.nav || {}

  return (
    <>
      <div className="grid grid--stats">
        <Stat icon={FiBriefcase} label="Funds" value={int(stats.total_funds)} tone="brand" />
        <Stat icon={FiLayers} label="Categories" value={int(stats.total_categories)} tone="brand" />
        <Stat icon={FiHome} label="AMCs" value={int(amcs.length - 1)} tone="accent" />
        <Stat
          icon={FiAward}
          label="Best YTD"
          value={pct(best.best)}
          note={`${int(best.reported_by)} funds reporting`}
          tone="up"
        />
        <Stat icon={FiTrendingUp} label="Mean YTD" value={pct(best.mean)} tone={direction(best.mean)} />
        <Stat
          icon={FiBriefcase}
          label="Median NAV"
          value={num(nav.median, 2)}
          note={`range ${num(nav.min, 2)} – ${num(nav.max, 2)}`}
          tone="flat"
        />
      </div>

      <Freshness freshness={body.freshness} label="MUFAP" />

      <Card
        icon={FiBriefcase}
        title="Funds"
        subtitle={`${filtered.length.toLocaleString()} matching`}
        flush
        actions={
          <Segmented options={PERIODS} value={period} onChange={changePeriod} label="Return period" />
        }
      >
        <div className="card-body" style={{ paddingBottom: 0 }}>
          <div className="toolbar">
            <div className="grow">
              <Search
                value={query}
                onChange={setQuery}
                placeholder="Fund or AMC…"
                label="Search funds"
              />
            </div>
            <Select
              icon={FiFilter}
              value={category}
              onChange={setCategory}
              options={categories}
              label="Filter by category"
            />
            <Select
              icon={FiHome}
              value={amc}
              onChange={setAmc}
              options={amcs}
              label="Filter by asset management company"
            />
          </div>
        </div>

        <DataTable
          columns={columns}
          rows={page.slice}
          rowKey={(fund, index) => `${fund.fund_name}-${fund.category}-${index}`}
          sort={sort}
          onSort={toggle}
          page={page}
          empty={{
            title: 'No funds match',
            hint: 'Clear the search or choose a different category.',
          }}
        />
      </Card>
    </>
  )
}
