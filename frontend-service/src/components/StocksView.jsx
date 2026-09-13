import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { CardSkeleton, StatSkeleton, Spinner, EmptyState } from './Skeleton'
import SearchRounded from '@mui/icons-material/SearchRounded'
import RefreshRounded from '@mui/icons-material/RefreshRounded'
import TrendingUpRounded from '@mui/icons-material/TrendingUpRounded'
import TrendingDownRounded from '@mui/icons-material/TrendingDownRounded'
import TrendingFlatRounded from '@mui/icons-material/TrendingFlatRounded'
import BoltRounded from '@mui/icons-material/BoltRounded'
import FormatListBulletedRounded from '@mui/icons-material/FormatListBulletedRounded'
import BarChartRounded from '@mui/icons-material/BarChartRounded'
import StorageRounded from '@mui/icons-material/StorageRounded'

const SUB_TABS = [
  { id: 'all',     label: 'All',     Icon: FormatListBulletedRounded },
  { id: 'gainers', label: 'Gainers', Icon: TrendingUpRounded },
  { id: 'losers',  label: 'Losers',  Icon: TrendingDownRounded },
  { id: 'active',  label: 'Active',  Icon: BoltRounded },
]

export default function StocksView({ refreshKey }) {
  const [stocks, setStocks] = useState([])
  const [summary, setSummary] = useState(null)
  const [sub, setSub] = useState('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [scraping, setScraping] = useState(false)

  const load = useCallback(async (view) => {
    setLoading(true)
    try {
      let dataPromise
      if (view === 'gainers') dataPromise = api.getGainers(50)
      else if (view === 'losers') dataPromise = api.getLosers(50)
      else if (view === 'active') dataPromise = api.getActive(50)
      else dataPromise = api.getStocks({ sort_by: 'volume', ascending: 'false', limit: 500 })

      const [data, sum] = await Promise.all([dataPromise, api.getSummary()])
      setStocks(data.data || [])
      setSummary(sum)
    } catch { setStocks([]) }
    setLoading(false)
  }, [])

  useEffect(() => { load(sub) }, [load, sub, refreshKey])

  const handleSearch = async () => {
    if (!search.trim()) return load(sub)
    setLoading(true)
    try {
      const res = await api.searchStocks(search.trim())
      setStocks(res.data || [])
    } catch { setStocks([]) }
    setLoading(false)
  }

  const handleScrape = async () => {
    setScraping(true)
    try { await api.scrapeStocks(); await load(sub) } catch {}
    setScraping(false)
  }

  const fmtN = (v) => v != null ? Number(v).toLocaleString() : '—'
  const fmtP = (v) => v != null ? Number(v).toFixed(2) : '—'
  const fmtVol = (v) => {
    if (v == null) return '—'
    if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B'
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
    return v.toLocaleString()
  }

  return (
    <div className="view">
      {/* PSX Header */}
      <div className="view-header">
        <div className="view-header-left">
          <img src="/icons/psx.png" alt="PSX" className="view-logo" />
          <div>
            <h2 className="view-title">PSX Stocks</h2>
            <p className="view-sub">Pakistan Stock Exchange</p>
          </div>
        </div>
        <button className="icon-btn" onClick={handleScrape} disabled={scraping} title="Refresh data">
          {scraping ? <Spinner /> : <RefreshRounded sx={{ fontSize: 20 }} />}
        </button>
      </div>

      {/* Stats */}
      {loading && !summary ? <StatSkeleton count={4} /> : summary && (
        <div className="stats-row four">
          <div className="stat-card">
            <div className="stat-label"><BarChartRounded sx={{ fontSize: 13 }} /> Total</div>
            <div className="stat-value">{summary.total_stocks ?? '—'}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label gain"><TrendingUpRounded sx={{ fontSize: 13 }} /> Up</div>
            <div className="stat-value gain">{summary.gainers ?? '—'}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label loss"><TrendingDownRounded sx={{ fontSize: 13 }} /> Down</div>
            <div className="stat-value loss">{summary.losers ?? '—'}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label"><TrendingFlatRounded sx={{ fontSize: 13 }} /> Flat</div>
            <div className="stat-value">{summary.unchanged ?? '—'}</div>
          </div>
        </div>
      )}

      {/* Sub-tabs */}
      <div className="sub-tabs">
        {SUB_TABS.map(t => (
          <button key={t.id} className={`sub-tab${sub === t.id ? ' active' : ''}`}
            onClick={() => { setSub(t.id); setSearch('') }}>
            <t.Icon sx={{ fontSize: 15 }} />
            <span>{t.label}</span>
          </button>
        ))}
      </div>

      {/* Search */}
      <div className="toolbar">
        <div className="search-box">
          <SearchRounded sx={{ fontSize: 18 }} className="search-icon" />
          <input
            type="text"
            placeholder="Search symbol (e.g. HBL)..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
          />
        </div>
      </div>

      {/* Count */}
      {!loading && stocks.length > 0 && (
        <div className="count-bar">{stocks.length} stocks</div>
      )}

      {/* List */}
      {loading ? <CardSkeleton count={8} /> : stocks.length === 0 ? (
        <EmptyState icon={StorageRounded} title="No stocks found" sub="Tap refresh to scrape PSX data" />
      ) : (
        <div className="card-list tight">
          {stocks.map((s, i) => {
            const up = s.change > 0
            const down = s.change < 0
            const cls = up ? 'gain' : down ? 'loss' : ''
            return (
              <div className="stock-row" key={i}>
                <div className="stock-symbol">{s.symbol}</div>
                <div className="stock-vol">{fmtVol(s.volume)}</div>
                <div className="stock-price">{fmtP(s.current)}</div>
                <div className={`stock-change ${cls}`}>
                  {up ? '+' : ''}{fmtP(s.change)} <span className="pct">({up ? '+' : ''}{fmtP(s.change_pct)}%)</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
