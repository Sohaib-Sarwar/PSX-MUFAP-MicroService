import { useMemo } from 'react'
import {
  IconDown,
  IconFall,
  IconFetched,
  IconFlat,
  IconPulse,
  IconRise,
  IconSector,
  IconStack,
  IconUp,
} from '../lib/icons'
import Freshness from '../components/Freshness'
import { Failed, Kpi, Loading, Panel } from '../components/ui'
import { useData } from '../lib/hooks'
import { clockShort, compact, compactOrDash, direction, int, num, pct, pkr, titleCase, until } from '../lib/format'

// A point move beside a percentage reads better signed and rounded.
const signedPts = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${num(v, 0)} pts`)


const RESOURCES = ['summary', 'indices', 'stocks', 'sectors', 'fundStats', 'funds']

export default function Overview({ nonce, onNavigate }) {
  const state = useData(RESOURCES, nonce)

  const view = useMemo(() => {
    if (state.status !== 'ready') return null
    const [summary, indices, stocks, sectors, fundStats, funds] = state.bodies

    const stockRows = stocks.data || []
    // Ranked only among instruments carrying today's quote.
    //
    // The screener reports a change for all 747 listed instruments, but for one
    // that did not trade today that change is left over from whenever it last
    // did. Ranking the whole list therefore put stale moves on a board labelled
    // as today's: GTECHBR at -93.55% on zero volume, FLYNGR1 at -88.89% on
    // zero volume, a board of one-paisa rights letters that had not traded.
    //
    // has_quote marks the rows with this session's own OHLC and volume, so
    // those are the only ones whose change describes today. The count is named
    // in the panel subtitle rather than left for the reader to infer.
    const withMove = stockRows.filter(
      (row) => row.has_quote && row.change_pct != null && row.change_pct !== 0
    )

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
      quotedCount: withMove.length,
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
      <div className="grid grid--kpi">
        <Kpi
          icon={IconUp}
          label="KSE 100"
          value={headline ? num(headline.current, 0) : '—'}
          note={headline ? `${pct(headline.change_pct)} · ${signedPts(headline.change)}` : 'index board unavailable'}
          tone={headline ? direction(headline.change) : 'flat'}
        />
        <Kpi
          icon={IconRise}
          label="Breadth"
          value={`${int(breadth.gainers)} / ${int(breadth.losers)}`}
          note={`up / down · ${int(breadth.unchanged)} flat`}
          tone={(breadth.gainers || 0) >= (breadth.losers || 0) ? 'up' : 'down'}
        />
        <Kpi icon={IconPulse} label="Volume" value={compact(breadth.total_volume)} note={pkr(breadth.total_traded_value)} />
        <Kpi
          icon={IconFetched}
          label="Fetched"
          value={clockShort(stocks.freshness?.fetched_at)}
          note={`next ${until(stocks.freshness?.next_refresh_at)}`}
        />
      </div>

      <div className="grid grid--split">
        <Freshness freshness={stocks.freshness} label="PSX" />
        <Freshness freshness={funds.freshness} label="MUFAP" />
      </div>

      <Panel
        icon={IconStack}
        title="Index board"
        sub={`${(indices.data || []).length} indices at the close`}
        end={
          <button type="button" className="btn btn--sm" onClick={() => onNavigate('indices')}>
            View all
          </button>
        }
      >
        <div className="grid grid--cards">
          {view.boards.map((index) => {
            const dir = direction(index.change)
            const Trend =
              dir === 'up' ? IconRise : dir === 'down' ? IconFall : IconFlat
            return (
              <article
                className="quote"
                key={index.index_name}
                style={{ '--tone': `var(--${dir})`, }}
              >
                <div className="quote-top">
                  <span className="quote-name">{index.index_name}</span>
                  <span className="quote-trend" aria-hidden="true">
                    <Trend />
                  </span>
                </div>
                <p className="quote-v">{num(index.current ?? index.value)}</p>
                <p className="quote-d">
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
      </Panel>

      <div className="grid grid--split">
        <MoverCard
          title="Top gainers"
          icon={IconUp}
          rows={view.gainers}
          quoted={view.quotedCount}
          tone="up"
          onNavigate={onNavigate}
        />
        <MoverCard
          title="Top losers"
          icon={IconDown}
          rows={view.losers}
          quoted={view.quotedCount}
          tone="down"
          onNavigate={onNavigate}
        />
      </div>

      <Panel
        icon={IconSector}
        title="Sector activity"
        sub="Largest sectors by combined market capitalisation"
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
                      {compactOrDash(sector.market_cap)}
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
      </Panel>
    </>
  )
}

function MoverCard({ title, icon, rows, quoted, tone, onNavigate }) {
  return (
    <Panel
      icon={icon}
      title={title}
      sub={`Among ${int(quoted)} instruments quoted today`}
      flush
      end={
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
                  <div className="cell-top">
                    <span className="rank">{position + 1}</span>
                    <span className="sym">{row.symbol}</span>
                  </div>
                  <span className="cell-sub">{row.name || row.sector || ''}</span>
                </td>
                <td className="right num">{num(row.current)}</td>
                <td className={`right delta delta--${tone}`}>
                  {pct(row.change_pct)}
                  <span className="sub">
                    {row.change > 0 ? '+' : ''}
                    {num(row.change)}
                  </span>
                </td>
                <td className="right num hide-sm">{compactOrDash(row.market_cap)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
