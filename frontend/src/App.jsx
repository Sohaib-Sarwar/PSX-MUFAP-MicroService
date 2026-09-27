import { useCallback, useEffect, useState } from 'react'
import {
  IconApi,
  IconClose,
  IconFunds,
  IconHome,
  IconIndices,
  IconMenu,
  IconRefresh,
  IconReload,
  IconSource,
  IconStocks,
  IconUp,
} from './lib/icons'
import { clearCache, knownCounts, load, REPO_URL } from './lib/client'
import { useRoute } from './lib/hooks'
import { int } from './lib/format'
import RefreshPanel from './components/RefreshPanel'
import Landing from './pages/Landing'
import Overview from './pages/Overview'
import Stocks from './pages/Stocks'
import Indices from './pages/Indices'
import Funds from './pages/Funds'
import ApiDocs from './pages/ApiDocs'

const PAGES = {
  home: {
    label: 'Home', icon: IconHome, title: 'PK Finance',
    blurb: 'Pakistan market data as a JSON API', component: Landing, bare: true,
  },
  overview: {
    label: 'Overview', icon: IconHome, title: 'Market overview',
    blurb: 'The session at a glance', component: Overview,
  },
  stocks: {
    label: 'Stocks', icon: IconStocks, title: 'Pakistan Stock Exchange',
    blurb: 'Every listed instrument', component: Stocks, count: 'psx.stocks',
  },
  indices: {
    label: 'Indices', icon: IconIndices, title: 'Index board',
    blurb: 'KSE100, KSE30, KMI30 and the rest', component: Indices, count: 'psx.indices',
  },
  funds: {
    label: 'Mutual funds', icon: IconFunds, title: 'MUFAP mutual funds',
    blurb: 'Daily NAV, loads and returns', component: Funds, count: 'mufap.funds',
  },
  api: {
    label: 'API reference', icon: IconApi, title: 'API reference',
    blurb: 'Endpoints, fields and the freshness contract', component: ApiDocs,
  },
}

const DATA_PAGES = ['overview', 'stocks', 'indices', 'funds']

export default function App() {
  const [route, navigate] = useRoute('home')
  const [nonce, setNonce] = useState(0)
  const [navOpen, setNavOpen] = useState(false)
  const [meta, setMeta] = useState(null)
  const [market, setMarket] = useState(null)
  const [refreshOpen, setRefreshOpen] = useState(false)

  const page = PAGES[route] || PAGES.home
  const Page = page.component

  useEffect(() => {
    let cancelled = false
    // Both feed chrome only; a failure here must not take the page with it.
    load('catalog').then((b) => !cancelled && setMeta(b)).catch(() => !cancelled && setMeta(null))
    load('marketStatus').then((b) => !cancelled && setMarket(b)).catch(() => !cancelled && setMarket(null))
    return () => {
      cancelled = true
    }
  }, [nonce])

  useEffect(() => {
    setNavOpen(false)
  }, [route])

  useEffect(() => {
    // The landing page is already called PK Finance; repeating it reads as a bug.
    document.title = page.bare ? 'PK Finance — Pakistan market data' : `${page.title} · PK Finance`
  }, [page.title, page.bare])

  // Reload re-reads what is published. Refresh goes and scrapes it again. Two
  // actions, two buttons — conflating them is what made a single button look
  // broken against a CDN cache.
  const reload = useCallback(() => {
    clearCache()
    setNonce((value) => value + 1)
  }, [])

  const go = useCallback((next) => navigate(next), [navigate])
  const status = market?.status

  return (
    <div className="shell">
      {navOpen && <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />}

      <aside className={navOpen ? 'nav is-open' : 'nav'}>
        <button type="button" className="brand" onClick={() => go('home')}>
          <span className="brand-mark" aria-hidden="true">
            <IconUp />
          </span>
          <span>
            <span className="brand-name" style={{ display: 'block' }}>
              PK Finance
            </span>
            <span className="brand-sub">Market data service</span>
          </span>
        </button>

        <p className="nav-label">Data</p>
        {DATA_PAGES.map((id) => (
          <NavItem key={id} id={id} active={route === id} onClick={go} meta={meta} />
        ))}

        <p className="nav-label">Developers</p>
        <NavItem id="api" active={route === 'api'} onClick={go} meta={meta} />
        <a className="nav-item" href={REPO_URL} target="_blank" rel="noreferrer noopener">
          <IconSource aria-hidden="true" />
          Source
        </a>

        <div className="nav-foot">
          <span>{meta?.version ? `v${meta.version}` : 'PK Finance'} · PSX and MUFAP</span>
          <span>Times in Pakistan Standard Time</span>
        </div>
      </aside>

      <div className="main">
        <header className="bar">
          <button
            type="button"
            className="btn btn--icon nav-toggle"
            aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
            aria-expanded={navOpen}
            onClick={() => setNavOpen((value) => !value)}
          >
            {navOpen ? <IconClose /> : <IconMenu />}
          </button>

          <div>
            <h1>{page.title}</h1>
            <p className="bar-sub hide-sm">{page.blurb}</p>
          </div>

          <div className="bar-right">
            {status && (
              <span className={`tag ${status === 'open' ? 'tag--up' : 'tag--line'} hide-sm`}>
                <span className={status === 'open' ? 'dot dot--live' : 'dot'} />
                PSX {status}
              </span>
            )}

            <button type="button" className="btn" onClick={reload} title="Re-read the published data">
              <IconReload aria-hidden="true" />
              <span className="hide-sm">Reload</span>
            </button>

            <button
              type="button"
              className="btn btn--primary"
              onClick={() => setRefreshOpen(true)}
              title="Scrape the sources again, now"
            >
              <IconRefresh aria-hidden="true" />
              <span className="hide-sm">Refresh</span>
            </button>
          </div>
        </header>

        {/* The landing page carries its own full-bleed rhythm, so it opts out
            of the padded grid the data screens share. */}
        {page.bare ? (
          <main key={route}>
            <Page nonce={nonce} onNavigate={go} />
          </main>
        ) : (
          <main className="page" key={route}>
            <Page nonce={nonce} onNavigate={go} />
          </main>
        )}
      </div>

      <RefreshPanel
        open={refreshOpen}
        onClose={() => setRefreshOpen(false)}
        counts={knownCounts()}
        onDone={reload}
      />
    </div>
  )
}

function NavItem({ id, active, onClick, meta }) {
  const page = PAGES[id]
  const Icon = page.icon
  const count = page.count ? meta?.datasets?.[page.count] : null

  return (
    <button
      type="button"
      className={active ? 'nav-item is-active' : 'nav-item'}
      aria-current={active ? 'page' : undefined}
      onClick={() => onClick(id)}
    >
      <Icon aria-hidden="true" />
      {page.label}
      {count ? <span className="nav-count">{int(count)}</span> : null}
    </button>
  )
}
