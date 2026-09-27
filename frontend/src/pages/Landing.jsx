import { useMemo } from 'react'
import {
  IconApi,
  IconArrowRight,
  IconClock,
  IconFetched,
  IconGlobe,
  IconShield,
  IconStack,
} from '../lib/icons'
import { Code, Failed, Loading, Note } from '../components/ui'
import { API_BASE } from '../lib/client'
import { useData } from '../lib/hooks'
import { clockShort, int, num, pct, until } from '../lib/format'

/**
 * The page someone lands on who has never seen this before.
 *
 * It answers three questions in order — what is this, is it current, how do I
 * use it — and every number on it is read from the live API rather than typed
 * in. A landing page that quotes figures it does not fetch goes stale the
 * first time the data moves, and this one is about data freshness.
 */
export default function Landing({ nonce, onNavigate }) {
  const state = useData(['catalog', 'summary', 'indices', 'fundStats', 'stocks'], nonce)

  const view = useMemo(() => {
    if (state.status !== 'ready') return null
    const [catalog, summary, indices, fundStats, stocks] = state.bodies
    return {
      catalog,
      summary,
      fundStats,
      fresh: stocks.freshness,
      kse: (indices.data || []).find((row) => row.index_name === 'KSE100'),
    }
  }, [state])

  if (state.status === 'loading') return <Loading rows={7} />
  if (state.status === 'error') return <Failed error={state.error} />

  const { catalog, summary, fundStats, fresh, kse } = view
  const datasets = catalog?.datasets || {}

  return (
    <div className="landing">
      <section className="hero">
        <p className="hero-eyebrow">
          <span className="tag tag--up">
            <span className="dot dot--live" />
            live
          </span>
          Pakistan Stock Exchange · Mutual Funds Association of Pakistan
        </p>

        <h1>
          Pakistan market data, <em>as a JSON API.</em>
        </h1>

        <p>
          Closing prices for every listed instrument and daily NAV for every mutual
          fund, scraped on a schedule, validated, and served as static files from a
          CDN. No key, no quota, no rate limit — and every response says exactly
          when it was fetched.
        </p>

        <div className="hero-actions">
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('overview')}>
            Open the dashboard
            <IconArrowRight aria-hidden="true" />
          </button>
          <button type="button" className="btn" onClick={() => onNavigate('api')}>
            <IconApi aria-hidden="true" />
            API reference
          </button>
        </div>

        <dl className="hero-stats">
          <div className="hero-stat">
            <dt>Instruments</dt>
            <dd>{int(datasets['psx.stocks'])}</dd>
            <small>{int(summary?.traded_instruments)} traded last session</small>
          </div>
          <div className="hero-stat">
            <dt>Mutual funds</dt>
            <dd>{int(datasets['mufap.funds'])}</dd>
            <small>{int(fundStats?.total_categories)} categories</small>
          </div>
          <div className="hero-stat">
            <dt>KSE 100</dt>
            <dd>{kse ? num(kse.current, 0) : '—'}</dd>
            <small>{kse ? `${pct(kse.change_pct)} on the session` : 'index board'}</small>
          </div>
          <div className="hero-stat">
            <dt>Last fetched</dt>
            <dd>{clockShort(fresh?.fetched_at)}</dd>
            <small>next {until(fresh?.next_refresh_at)}</small>
          </div>
        </dl>
      </section>

      <div className="strip">
        <div className="strip-inner">
          <h2>What it gives you</h2>
          <div className="feature-row">
            <div className="feature">
              <IconStack aria-hidden="true" />
              <h3>Every instrument, every fund</h3>
              <p>
                Price, percentage move, market capitalisation, P/E, dividend yield and
                free float per instrument. NAV, offer and repurchase prices, all three
                sales loads, rating, trustee and eleven return periods per fund.
              </p>
            </div>
            <div className="feature">
              <IconClock aria-hidden="true" />
              <h3>Honest about its age</h3>
              <p>
                Every response carries when it was fetched, what the source dates it,
                when the next run is due, and whether that run is overdue. Nothing has
                to be inferred from a timestamp.
              </p>
            </div>
            <div className="feature">
              <IconGlobe aria-hidden="true" />
              <h3>Addressable by path</h3>
              <p>
                One file per instrument, per fund, per sector, per index and per
                category. Fetch {int(datasets['psx.stocks'])} rows, or fetch the one
                you wanted at about 700 bytes.
              </p>
            </div>
            <div className="feature">
              <IconShield aria-hidden="true" />
              <h3>Validated before publishing</h3>
              <p>
                Impossible rows are rejected, odd-but-real ones are flagged, and a
                batch whose row count collapses is refused outright. A parser
                regression cannot quietly replace the data.
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="strip">
        <div className="strip-inner">
          <h2>Try it</h2>
          <p>
            Plain <code>GET</code>, CORS open to any origin, gzipped by the CDN.
          </p>
          <div className="endpoint-sample">
            <Code
              code={`# The whole universe, or one instrument
curl -s ${API_BASE}/psx/stocks/ogdc.json

# Poll this before re-downloading anything — under 1 KB
curl -s ${API_BASE}/freshness.json`}
            />
          </div>
          <Note icon={IconFetched} variant="ok">
            Last published{' '}
            <strong>{clockShort(catalog?.published_at)} PKT</strong>. PSX refreshes at
            17:00 PKT on working days; MUFAP hourly between 18:00 and 00:00. The next
            run is due <strong>{until(fresh?.next_refresh_at)}</strong>.
          </Note>
        </div>
      </div>
    </div>
  )
}
