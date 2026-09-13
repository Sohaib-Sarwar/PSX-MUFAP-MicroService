import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { CardSkeleton, Spinner, EmptyState } from './Skeleton'
import RefreshRounded from '@mui/icons-material/RefreshRounded'
import TrendingUpRounded from '@mui/icons-material/TrendingUpRounded'
import TrendingDownRounded from '@mui/icons-material/TrendingDownRounded'
import InsightsRounded from '@mui/icons-material/InsightsRounded'

export default function IndicesView({ refreshKey }) {
  const [indices, setIndices] = useState([])
  const [loading, setLoading] = useState(true)
  const [scraping, setScraping] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.getIndices()
      setIndices(res.data || [])
    } catch { setIndices([]) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load, refreshKey])

  const handleScrape = async () => {
    setScraping(true)
    try { await api.scrapeIndices(); await load() } catch {}
    setScraping(false)
  }

  const fmtN = (v) => v != null ? Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'

  return (
    <div className="view">
      <div className="view-header">
        <div>
          <h2 className="view-title">Market Indices</h2>
          <p className="view-sub">PSX index performance</p>
        </div>
        <button className="icon-btn" onClick={handleScrape} disabled={scraping}>
          {scraping ? <Spinner /> : <RefreshRounded sx={{ fontSize: 20 }} />}
        </button>
      </div>

      {loading ? <CardSkeleton count={6} /> : indices.length === 0 ? (
        <EmptyState icon={InsightsRounded} title="No index data" sub="Tap refresh to scrape indices" />
      ) : (
        <div className="index-grid">
          {indices.map((idx, i) => {
            const change = idx.change ?? idx.Change ?? 0
            const up = change > 0
            const down = change < 0
            const cls = up ? 'gain' : down ? 'loss' : ''
            const name = idx.name || idx.Name || idx.index || '—'
            const current = idx.current || idx.Current || idx.value || 0
            const changePct = idx.change_pct ?? idx['Change%'] ?? 0

            return (
              <div className={`index-card ${cls}`} key={i}>
                <div className="idx-name">{name}</div>
                <div className="idx-val">{fmtN(current)}</div>
                <div className={`idx-change ${cls}`}>
                  {up ? <TrendingUpRounded sx={{ fontSize: 15 }} /> : down ? <TrendingDownRounded sx={{ fontSize: 15 }} /> : null}
                  <span>{up ? '+' : ''}{fmtN(change)}</span>
                  <span className="pct">({up ? '+' : ''}{Number(changePct).toFixed(2)}%)</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
