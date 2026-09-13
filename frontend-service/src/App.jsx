import { useState, useEffect, useCallback } from 'react'
import Header from './components/Header'
import BottomNav from './components/BottomNav'
import SideNav from './components/SideNav'
import PullToRefresh from './components/PullToRefresh'
import FundsView from './components/FundsView'
import StocksView from './components/StocksView'
import IndicesView from './components/IndicesView'
import { api, clearAllCache } from './api'

export default function App() {
  const [tab, setTab] = useState(() => {
    const saved = localStorage.getItem('fintraxa-tab')
    return ['funds', 'stocks', 'indices'].includes(saved) ? saved : 'funds'
  })
  const [health, setHealth] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const handlePullRefresh = useCallback(async () => {
    clearAllCache()
    setRefreshKey(k => k + 1)
    // Brief delay so views can show loading state
    await new Promise(r => setTimeout(r, 600))
  }, [])

  const changeTab = useCallback((t) => {
    setTab(t)
    localStorage.setItem('fintraxa-tab', t)
  }, [])

  const checkHealth = useCallback(() => {
    api.health()
      .then(d => setHealth(d))
      .catch(() => setHealth(null))
  }, [])

  useEffect(() => {
    checkHealth()
    const id = setInterval(checkHealth, 60000)
    return () => clearInterval(id)
  }, [checkHealth])

  return (
    <div className="app">
      <Header health={health} />
      <div className="app-body">
        <SideNav tab={tab} onChange={changeTab} />
        <main className="main-content">
          <PullToRefresh onRefresh={handlePullRefresh}>
            <div style={{ display: tab === 'funds' ? 'block' : 'none' }}><FundsView refreshKey={refreshKey} /></div>
            <div style={{ display: tab === 'stocks' ? 'block' : 'none' }}><StocksView refreshKey={refreshKey} /></div>
            <div style={{ display: tab === 'indices' ? 'block' : 'none' }}><IndicesView refreshKey={refreshKey} /></div>
          </PullToRefresh>
        </main>
      </div>
      <BottomNav tab={tab} onChange={changeTab} />
    </div>
  )
}
