import { useCallback, useEffect, useState } from 'react'
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
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <span className="brand-name">Fintraxa</span>
        </div>
        <div className="topbar-right">
          {market && (
            <span className={`market market--${market.status}`}>
              <span className="dot" aria-hidden="true" />
              PSX {market.status}
            </span>
          )}
          <button className="ghost" onClick={refresh} title="Reload data">
            Refresh
          </button>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            role="tab"
            aria-selected={tab === t.id}
            className={`tab${tab === t.id ? ' is-active' : ''}`}
            onClick={() => changeTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="main">
        {tab === 'stocks' && <StocksView nonce={nonce} />}
        {tab === 'funds' && <FundsView nonce={nonce} />}
        {tab === 'indices' && <IndicesView nonce={nonce} />}
      </main>

      <footer className="foot">
        <span>
          Data: <a href="https://dps.psx.com.pk" target="_blank" rel="noreferrer">PSX</a>
          {' · '}
          <a href="https://www.mufap.com.pk" target="_blank" rel="noreferrer">MUFAP</a>
        </span>
      </footer>
    </div>
  )
}

export { Freshness }
