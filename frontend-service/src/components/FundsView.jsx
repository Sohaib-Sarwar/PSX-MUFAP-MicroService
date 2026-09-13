import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { CardSkeleton, StatSkeleton, Spinner, EmptyState } from './Skeleton'
import SearchRounded from '@mui/icons-material/SearchRounded'
import RefreshRounded from '@mui/icons-material/RefreshRounded'
import AccountBalanceWalletRounded from '@mui/icons-material/AccountBalanceWalletRounded'
import CategoryRounded from '@mui/icons-material/CategoryRounded'
import TrendingUpRounded from '@mui/icons-material/TrendingUpRounded'
import CalendarTodayRounded from '@mui/icons-material/CalendarTodayRounded'
import StorageRounded from '@mui/icons-material/StorageRounded'

export default function FundsView({ refreshKey }) {
  const [funds, setFunds] = useState([])
  const [stats, setStats] = useState(null)
  const [categories, setCategories] = useState([])
  const [activeCategory, setActiveCategory] = useState(null)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [scraping, setScraping] = useState(false)

  const load = useCallback(async (cat) => {
    setLoading(true)
    try {
      const params = { sort_by: 'nav', ascending: 'false', limit: 2000 }
      if (cat) params.category = cat
      const [fundsRes, statsRes, catsRes] = await Promise.all([
        api.getFunds(params),
        api.getFundStats(),
        api.getCategories(),
      ])
      setFunds(fundsRes.data || [])
      setStats(statsRes)
      setCategories(
        (catsRes.categories || [])
          .map(c => typeof c === 'object' && c !== null ? c.category : c)
          .filter(Boolean)
      )
    } catch { setFunds([]) }
    setLoading(false)
  }, [])

  useEffect(() => { load(activeCategory) }, [load, activeCategory, refreshKey])

  const handleSearch = async () => {
    if (!search.trim()) return load(activeCategory)
    setLoading(true)
    try {
      const res = await api.searchFunds(search.trim())
      setFunds(res.data || [])
    } catch { setFunds([]) }
    setLoading(false)
  }

  const handleScrape = async () => {
    setScraping(true)
    try { await api.scrapeFunds(); await load(activeCategory) }
    catch {}
    setScraping(false)
  }

  const filtered = funds
  const fmt = (v) => v != null ? Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—'

  return (
    <div className="view">
      {/* MUFAP Header */}
      <div className="view-header">
        <div className="view-header-left">
          <img src="/icons/mutual.png" alt="MUFAP" className="view-logo" />
          <div>
            <h2 className="view-title">Mutual Funds</h2>
            <p className="view-sub">MUFAP Pakistan</p>
          </div>
        </div>
        <button className="icon-btn" onClick={handleScrape} disabled={scraping} title="Refresh data">
          {scraping ? <Spinner /> : <RefreshRounded sx={{ fontSize: 20 }} />}
        </button>
      </div>

      {/* Stats */}
      {loading && !stats ? <StatSkeleton count={3} /> : stats && (
        <div className="stats-row">
          <div className="stat-card">
            <div className="stat-label"><AccountBalanceWalletRounded sx={{ fontSize: 13 }} /> Funds</div>
            <div className="stat-value">{stats.total_funds?.toLocaleString() || '—'}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label"><CategoryRounded sx={{ fontSize: 13 }} /> Categories</div>
            <div className="stat-value">{stats.total_categories || '—'}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label"><TrendingUpRounded sx={{ fontSize: 13 }} /> Avg NAV</div>
            <div className="stat-value">{stats.nav ? fmt(stats.nav.mean) : '—'}</div>
          </div>
        </div>
      )}

      {/* Search */}
      <div className="toolbar">
        <div className="search-box">
          <SearchRounded sx={{ fontSize: 18 }} className="search-icon" />
          <input
            type="text"
            placeholder="Search funds..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
          />
        </div>
      </div>

      {/* Category filter — chips on mobile, dropdown on desktop */}
      {categories.length > 0 && (
        <>
          <div className="chips-scroll mobile-only">
            <button
              className={`chip${!activeCategory ? ' active' : ''}`}
              onClick={() => { setActiveCategory(null) }}
            >All</button>
            {categories.map(c => (
              <button
                key={c}
                className={`chip${activeCategory === c ? ' active' : ''}`}
                onClick={() => setActiveCategory(activeCategory === c ? null : c)}
              >{String(c).replace(/ Fund$/i, '')}</button>
            ))}
          </div>
          <div className="category-select-wrap desktop-only">
            <CategoryRounded sx={{ fontSize: 16 }} className="select-icon" />
            <select
              className="category-select"
              value={activeCategory || ''}
              onChange={e => setActiveCategory(e.target.value || null)}
            >
              <option value="">All Categories ({categories.length})</option>
              {categories.map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
        </>
      )}

      {/* Count */}
      {!loading && filtered.length > 0 && (
        <div className="count-bar">{filtered.length} funds</div>
      )}

      {/* List */}
      {loading ? <CardSkeleton count={6} /> : filtered.length === 0 ? (
        <EmptyState icon={StorageRounded} title="No funds found" sub="Tap refresh to scrape latest data" />
      ) : (
        <div className="card-list">
          {filtered.map((f, i) => (
            <div className="data-card" key={i}>
              <div className="card-top">
                <div className="card-info">
                  <span className="card-title">{f.fund_name}</span>
                  <span className="card-sub">{f.fund_category}</span>
                </div>
                <div className="card-value-block">
                  <span className="card-value">{fmt(f.nav)}</span>
                  <span className="card-value-label">NAV</span>
                </div>
              </div>
              <div className="card-bottom">
                <span className="card-meta">
                  <CalendarTodayRounded sx={{ fontSize: 11 }} /> {f.nav_date || '—'}
                </span>
                <span className="card-meta">Offer: {fmt(f.offer_price)}</span>
                <span className="card-meta">Repurchase: {fmt(f.repurchase_price)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
