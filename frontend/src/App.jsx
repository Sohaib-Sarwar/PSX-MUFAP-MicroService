import { useCallback, useEffect, useState } from 'react'
import {
  FiActivity,
  FiBarChart2,
  FiBookOpen,
  FiBriefcase,
  FiCompass,
  FiRefreshCw,
  FiSettings,
  FiShield,
  FiTrendingUp,
  FiTrendingDown,
} from 'react-icons/fi'
import { api, clearCache } from './api'
import StocksView from './components/StocksView'
import FundsView from './components/FundsView'
import IndicesView from './components/IndicesView'
import Freshness from './components/Freshness'

const TABS = [
  { id: 'stocks', label: 'Stocks' },
  { id: 'funds', label: 'Mutual Funds' },
  { id: 'indices', label: 'Indices' },
]

export default function App() {
  const [tab, setTab] = useState(() => {
    try {
      const saved = localStorage.getItem('fintraxa.tab')
      return TABS.some((t) => t.id === saved) ? saved : 'stocks'
    } catch {
      return 'stocks'
    }
  })
  const [market, setMarket] = useState(null)
  const [nonce, setNonce] = useState(0)

  const changeTab = useCallback((id) => {
    setTab(id)
    try {
      localStorage.setItem('fintraxa.tab', id)
    } catch {
      // private browsing — the tab just will not persist
    }
  }, [])

  const refresh = useCallback(() => {
    clearCache()
    setNonce((n) => n + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .marketStatus()
        .then((d) => !cancelled && setMarket(d))
        .catch(() => !cancelled && setMarket(null))
    load()
    const id = setInterval(load, 120_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand-mark" aria-label="PK Finance brand">
            <img src="/fintraxa.png" alt="PK Finance logo" />
          </div>
          <div className="brand-text-wrap">
            <span className="brand-title">PK Finance</span>
            <span className="brand-sub">Dashboard</span>
          </div>
        </div>

        <nav className="nav-tabs" role="tablist" aria-label="Main navigation">
          {TABS.map((t) => {
            const Icon =
              t.id === 'funds' ? FiBriefcase :
              t.id === 'stocks' ? FiBarChart2 :
              FiBookOpen

            return (
              <button
                key={t.id}
                id={`tab-${t.id}`}
                role="tab"
                aria-selected={tab === t.id}
                className={`nav-tab${tab === t.id ? ' is-active' : ''}`}
                onClick={() => changeTab(t.id)}
              >
                <Icon className="nav-icon" aria-hidden="true" />
                {t.label}
              </button>
            )
          })}
          <button className="nav-tab nav-tab--settings" type="button" aria-label="Settings">
            <FiSettings className="nav-icon" aria-hidden="true" />
            Config
          </button>
        </nav>

        <div className="topbar-meta">
          <span className="meta-chip"><FiShield /> Integrity</span>
          <span className="meta-chip"><FiActivity /> Live</span>
          <span className="meta-chip"><FiCompass /> Market</span>
        </div>

        <div className="topbar-right">
          {market && (
            <span className={`market-pill market-pill--${market.status}`}>
              {market.status === 'open' ? <FiTrendingUp aria-hidden="true" /> : market.status === 'closed' ? <FiTrendingDown aria-hidden="true" /> : <FiActivity aria-hidden="true" />}
              <span>{market.status === 'open' ? 'MUFAP' : 'PSX'}</span>
            </span>
          )}
          <button className="ghost-button" onClick={refresh} title="Reload data" type="button">
            <FiRefreshCw className="button-icon" />
            Refresh
          </button>
        </div>
      </header>

      <main className="main-shell">
        {tab === 'stocks' && <StocksView nonce={nonce} />}
        {tab === 'funds' && <FundsView nonce={nonce} />}
        {tab === 'indices' && <IndicesView nonce={nonce} />}
      </main>
    </div>
  )
}

export { Freshness }
