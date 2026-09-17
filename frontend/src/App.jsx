import { useCallback, useEffect, useState } from 'react'
import {
  FiBarChart2,
  FiBriefcase,
  FiCode,
  FiGithub,
  FiGrid,
  FiHome,
  FiMenu,
  FiMoon,
  FiRefreshCw,
  FiSun,
  FiTrendingUp,
  FiX,
} from 'react-icons/fi'
import { clearCache, load, REPO_URL } from './lib/client'
import { useRoute, useTheme } from './lib/hooks'
import { int } from './lib/format'
import Overview from './pages/Overview'
import Stocks from './pages/Stocks'
import Indices from './pages/Indices'
import Funds from './pages/Funds'
import ApiDocs from './pages/ApiDocs'

const PAGES = {
  overview: { label: 'Overview', icon: FiHome, title: 'Market overview',
              blurb: 'PSX close and MUFAP NAVs at a glance', component: Overview },
  stocks: { label: 'Stocks', icon: FiBarChart2, title: 'Pakistan Stock Exchange',
            blurb: 'Every listed instrument at the close', component: Stocks,
            count: 'psx.stocks' },
  indices: { label: 'Indices', icon: FiGrid, title: 'Index board',
             blurb: 'KSE100, KSE30, KMI30 and the rest', component: Indices,
             count: 'psx.indices' },
  funds: { label: 'Mutual funds', icon: FiBriefcase, title: 'MUFAP mutual funds',
           blurb: 'Daily NAV, loads and returns by fund', component: Funds,
           count: 'mufap.funds' },
  api: { label: 'API reference', icon: FiCode, title: 'API reference',
         blurb: 'Endpoints, schemas and the freshness contract', component: ApiDocs },
}

const ORDER = ['overview', 'stocks', 'indices', 'funds', 'api']

export default function App() {
  const [route, navigate] = useRoute('overview')
  const [theme, toggleTheme] = useTheme()
  const [nonce, setNonce] = useState(0)
  const [menuOpen, setMenuOpen] = useState(false)
  const [meta, setMeta] = useState(null)
  const [market, setMarket] = useState(null)

  const page = PAGES[route] || PAGES.overview
  const Page = page.component

  useEffect(() => {
    let cancelled = false
    // Both feed chrome only — a failure here must not take the page with it.
    load('catalog')
      .then((body) => !cancelled && setMeta(body))
      .catch(() => !cancelled && setMeta(null))
    load('marketStatus')
      .then((body) => !cancelled && setMarket(body))
      .catch(() => !cancelled && setMarket(null))
    return () => {
      cancelled = true
    }
  }, [nonce])

  useEffect(() => {
    setMenuOpen(false)
  }, [route])

  useEffect(() => {
    document.title = `${page.title} · PK Finance`
  }, [page.title])

  const refresh = useCallback(() => {
    clearCache()
    setNonce((value) => value + 1)
  }, [])

  const go = useCallback(
    (next) => {
      navigate(next)
    },
    [navigate]
  )

  const status = market?.status

  return (
    <div className="shell">
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} aria-hidden="true" />}

      <aside className={menuOpen ? 'sidebar is-open' : 'sidebar'}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <FiTrendingUp />
          </span>
          <div>
            <div className="brand-name">PK Finance</div>
            <div className="brand-sub">Market data service</div>
          </div>
        </div>

        <p className="nav-label">Data</p>
        {ORDER.slice(0, 4).map((id) => (
          <NavItem key={id} id={id} active={route === id} onClick={go} meta={meta} />
        ))}

        <p className="nav-label">Developers</p>
        <NavItem id="api" active={route === 'api'} onClick={go} meta={meta} />
        <a className="nav-item" href={REPO_URL} target="_blank" rel="noreferrer noopener">
          <FiGithub aria-hidden="true" />
          Source
        </a>

        <div className="sidebar-foot">
          <span>
            {meta?.version ? `v${meta.version}` : 'PK Finance'} · data from PSX and MUFAP
          </span>
          <span>Times shown in Pakistan Standard Time.</span>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="btn btn--icon menu-button"
            aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
          >
            {menuOpen ? <FiX /> : <FiMenu />}
          </button>

          <div>
            <h1>{page.title}</h1>
            <p className="topbar-sub hide-sm">{page.blurb}</p>
          </div>

          <div className="topbar-right">
            {status && (
              <span
                className={`badge badge--${status === 'open' ? 'up' : 'outline'} hide-sm`}
                title={market.last_tick ? `Last tick ${market.last_tick}` : undefined}
              >
                <span className={status === 'open' ? 'dot dot--pulse' : 'dot'} />
                PSX {status}
              </span>
            )}

            <button
              type="button"
              className="btn btn--icon"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
              title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
            >
              {theme === 'dark' ? <FiSun /> : <FiMoon />}
            </button>

            <button type="button" className="btn btn--primary" onClick={refresh}>
              <FiRefreshCw aria-hidden="true" />
              <span className="hide-sm">Reload</span>
            </button>
          </div>
        </header>

        <main className="page" key={route}>
          <Page nonce={nonce} onNavigate={go} />
        </main>
      </div>
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
