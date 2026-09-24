import { useMemo } from 'react'
import {
  FiActivity,
  FiArrowDownRight,
  FiArrowUpRight,
  FiAward,
  FiBarChart2,
  FiBriefcase,
  FiClock,
  FiLayers,
  FiMinus,
  FiPieChart,
  FiTrendingDown,
  FiTrendingUp,
} from 'react-icons/fi'
import Freshness from '../components/Freshness'
import { Card, Failed, Loading, Stat } from '../components/ui'
import { useData } from '../lib/hooks'
import { ago, clock, compact, direction, int, num, pct, pkr, titleCase } from '../lib/format'

// The tile is narrow; the strip under each table carries the full instant.
const shortClock = (value) => clock(value).replace(/^(\d{2} \w{3}) \d{4},/, '$1')

const RESOURCES = ['summary', 'indices', 'stocks', 'sectors', 'fundStats', 'funds']

export default function Overview({ nonce, onNavigate }) {
  const state = useData(RESOURCES, nonce)

  const view = useMemo(() => {
    if (state.status !== 'ready') return null
    const [summary, indices, stocks, sectors, fundStats, funds] = state.bodies

    const stockRows = stocks.data || []
    const withMove = stockRows.filter((row) => row.change_pct != null && row.change_pct !== 0)

    const rank = (ascending) =>
      [...withMove].sort((a, b) =>
        ascending ? a.change_pct - b.change_pct : b.change_pct - a.change_pct
      )

    const headline = (indices.data || []).find((row) => row.index_name === 'KSE100')
    const bestFund = [...(funds.data || [])]
      .filter((fund) => fund.returns?.ytd != null)
      .sort((a, b) => b.returns.ytd - a.returns.ytd)[0]

    return {
      summary,
      indices,
      stocks,
      funds,
      fundStats,
      headline,
      bestFund,
      gainers: rank(false).slice(0, 6),
      losers: rank(true).slice(0, 6),
      boards: (indices.data || []).slice(0, 6),
      sectors: (sectors.data || []).slice(0, 8),
    }
  }, [state])

  if (state.status === 'loading') return <Loading rows={9} />
  if (state.status === 'error') return <Failed error={state.error} />

  const { summary, indices, stocks, funds, fundStats, headline, bestFund } = view
  const breadth = summary || {}
  const stats = fundStats || {}

  return (
    <>
      <div className="grid grid--stats">
        <Stat
          icon={FiTrendingUp}
          label="KSE 100"
          value={headline ? num(headline.current, 2) : '—'}
          note={
            headline
              ? `${pct(headline.change_pct)} · ${headline.change > 0 ? '+' : ''}${num(headline.change)} pts`
              : 'Index board unavailable'
          }
          tone={headline ? direction(headline.change) : 'flat'}
        />
        <Stat
          icon={FiArrowUpRight}
          label="Advancing"
          value={int(breadth.gainers)}
          note={`${int(breadth.losers)} declining · ${int(breadth.unchanged)} flat`}
          tone="up"
        />
        <Stat
          icon={FiActivity}
          label="Volume"
          value={compact(breadth.total_volume)}
          note={`${pkr(breadth.total_traded_value)} traded`}
          tone="brand"
        />
        <Stat
          icon={FiClock}
          label="Data fetched"
          value={shortClock(stocks.freshness?.fetched_at)}
          note={`PSX · ${ago((Date.now() - Date.parse(stocks.freshness?.fetched_at || 0)) / 1000)}`}
          tone="brand"
        />
        <Stat
          icon={FiBarChart2}
          label="Instruments"
          value={int(breadth.traded_instruments)}
          note={`traded of ${int(breadth.listed_instruments)} listed`}
          tone="brand"
        />
        <Stat
          icon={FiBriefcase}
          label="Mutual funds"
          value={int(stats.total_funds)}
          note={`${int(stats.total_categories)} categories`}
          tone="accent"
        />
        <Stat
          icon={FiAward}
          label="Best fund YTD"
          value={bestFund ? pct(bestFund.returns.ytd) : '—'}
          note={bestFund ? bestFund.fund_name : 'No return data'}
          tone="up"
        />
      </div>

      <div className="grid grid--halves">
        <Freshness freshness={stocks.freshness} label="PSX" />
        <Freshness freshness={funds.freshness} label="MUFAP" />
      </div>

      <Card
        icon={FiLayers}
        title="Index board"
        subtitle={`${(indices.data || []).length} indices at the close`}
        actions={
          <button type="button" className="btn btn--sm" onClick={() => onNavigate('indices')}>
            View all
          </button>
        }
      >
        <div className="grid grid--cards">
          {view.boards.map((index) => {
            const dir = direction(index.change)
            const Trend =
              dir === 'up' ? FiArrowUpRight : dir === 'down' ? FiArrowDownRight : FiMinus
            return (
              <article
                className="quote"
                key={index.index_name}
                style={{ '--tone': `var(--${dir})`, '--tone-soft': `var(--${dir}-soft)` }}
              >
                <div className="quote-top">
                  <span className="quote-name">{index.index_name}</span>
                  <span className="quote-trend" aria-hidden="true">
                    <Trend />
                  </span>
                </div>
                <p className="quote-value">{num(index.current ?? index.value)}</p>
                <p className="quote-change">
                  {index.change > 0 ? '+' : ''}
                  {num(index.change)} · {pct(index.change_pct)}
                </p>
                {index.high != null && (
                  <p className="quote-meta">
                    <span>H {num(index.high)}</span>
                    <span>L {num(index.low)}</span>
                  </p>
                )}
              </article>
            )
          })}
        </div>
      </Card>

      <div className="grid grid--halves">
        <MoverCard
          title="Top gainers"
          icon={FiTrendingUp}
          rows={view.gainers}
          tone="up"
          onNavigate={onNavigate}
        />
        <MoverCard
          title="Top losers"
          icon={FiTrendingDown}
          rows={view.losers}
          tone="down"
          onNavigate={onNavigate}
        />
      </div>

      <Card
        icon={FiPieChart}
        title="Sector activity"
        subtitle="Largest sectors by combined market capitalisation"
        flush
      >
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Sector</th>
                <th scope="col" className="right">Instruments</th>
                <th scope="col" className="right hide-sm">Advancing</th>
                <th scope="col" className="right hide-sm">Declining</th>
                <th scope="col" className="right">Market cap</th>
                <th scope="col" className="right">Avg move</th>
              </tr>
            </thead>
            <tbody>
              {view.sectors.map((sector) => {
                const dir = direction(sector.avg_change_pct)
                const share = view.sectors[0].market_cap
                  ? (sector.market_cap / view.sectors[0].market_cap) * 100
                  : 0
                return (
                  <tr key={sector.sector}>
                    <td>{titleCase(sector.sector)}</td>
                    <td className="right num">{int(sector.instruments)}</td>
                    <td className="right num hide-sm delta--up">{int(sector.gainers)}</td>
                    <td className="right num hide-sm delta--down">{int(sector.losers)}</td>
                    <td className="right num barcell">
                      {compact(sector.market_cap)}
                      <span
                        className="bar"
                        style={{ width: `${Math.max(share * 0.6, 2)}px`, '--tone': `var(--${dir})` }}
                      />
                    </td>
                    <td className={`right delta delta--${dir}`}>{pct(sector.avg_change_pct)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}

function MoverCard({ title, icon, rows, tone, onNavigate }) {
  return (
    <Card
      icon={icon}
      title={title}
      subtitle="By percentage move at the close"
      flush
      actions={
        <button type="button" className="btn btn--sm" onClick={() => onNavigate('stocks')}>
          All stocks
        </button>
      }
    >
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Symbol</th>
              <th scope="col" className="right">Price</th>
              <th scope="col" className="right">Change</th>
              <th scope="col" className="right hide-sm">Market cap</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, position) => (
              <tr key={row.symbol}>
                <td>
                  <div className="cell-primary">
                    <span className="rank">{position + 1}</span>
                    <span className="sym">{row.symbol}</span>
                  </div>
                  <span className="cell-sub">{row.name || row.sector || ''}</span>
                </td>
                <td className="right num">{num(row.current)}</td>
                <td className={`right delta delta--${tone}`}>
                  {pct(row.change_pct)}
                  <span className="pct">
                    {row.change > 0 ? '+' : ''}
                    {num(row.change)}
                  </span>
                </td>
                <td className="right num hide-sm">{compact(row.market_cap)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
