import { useMemo, useState } from 'react'
import {
  FiBook,
  FiChevronRight,
  FiClock,
  FiCode,
  FiDatabase,
  FiExternalLink,
  FiGithub,
  FiInfo,
  FiPlay,
  FiServer,
  FiShield,
  FiTerminal,
} from 'react-icons/fi'
import { Callout, Card, Copy, CodeBlock, Failed, Loading, Segmented } from '../components/ui'
import { API_BASE, IS_LIVE, REPO_URL, absoluteUrl } from '../lib/client'
import { useData } from '../lib/hooks'
import { int, moment } from '../lib/format'

const LANGUAGES = [
  { id: 'curl', label: 'curl' },
  { id: 'js', label: 'JavaScript' },
  { id: 'python', label: 'Python' },
  { id: 'pandas', label: 'pandas' },
]

/** `{period}` is a real placeholder in the catalog; a link needs a real value. */
const concrete = (path) => path.replace('{period}', 'ytd')

export default function ApiDocs({ nonce }) {
  const state = useData(['catalog'], nonce)
  const [language, setLanguage] = useState('curl')

  const catalog = state.status === 'ready' ? state.bodies[0] : null
  const base = useMemo(() => (catalog?.base_url || absoluteUrl('')).replace(/\/$/, ''), [catalog])

  if (state.status === 'loading') return <Loading rows={8} />
  if (state.status === 'error') return <Failed error={state.error} />

  const samples = {
    curl: `# Every endpoint is a GET with no authentication.
curl -s ${base}/psx/stocks/gainers.json | jq '.data[0]'

# The freshness envelope travels with the data, never separately.
curl -s ${base}/mufap/funds.json | jq '.freshness'`,

    js: `const base = '${base}'

const response = await fetch(\`\${base}/psx/stocks.json\`)
const body = await response.json()

// Age is measured when the file is written and cannot tick on a static host,
// so work it out from fetched_at instead.
const ageSeconds = (Date.now() - Date.parse(body.freshness.fetched_at)) / 1000
const isStale = ageSeconds > body.freshness.stale_after_seconds

const movers = body.data
  .filter((row) => row.traded && row.change_pct > 3)
  .sort((a, b) => b.change_pct - a.change_pct)

console.log(body.freshness.data_as_of, isStale, movers.length)`,

    python: `import urllib.request, json

BASE = "${base}"

with urllib.request.urlopen(f"{BASE}/mufap/funds.json") as response:
    body = json.load(response)

print(body["freshness"]["state"], body["freshness"]["data_as_of"])

money_market = [
    fund for fund in body["data"]
    if fund["category"] == "Money Market" and fund["returns"]["ytd"] is not None
]
money_market.sort(key=lambda fund: fund["returns"]["ytd"], reverse=True)

for fund in money_market[:5]:
    print(f'{fund["returns"]["ytd"]:6.2f}%  {fund["fund_name"]}')`,

    pandas: `import pandas as pd

BASE = "${base}"

stocks = pd.json_normalize(pd.read_json(f"{BASE}/psx/stocks.json")["data"])
traded = stocks[stocks["traded"]]

print(traded.nlargest(10, "volume")[["symbol", "name", "current", "change_pct", "volume"]])

funds = pd.json_normalize(pd.read_json(f"{BASE}/mufap/funds.json")["data"])
print(funds.groupby("category")["returns.ytd"].mean().sort_values(ascending=False))`,
  }

  const endpoints = catalog?.endpoints || []
  const schedules = catalog?.schedules || {}

  return (
    <div className="doc">
      <section className="doc-hero">
        <h2>API reference</h2>
        <p>
          {catalog?.description ||
            'Pakistan Stock Exchange and MUFAP mutual fund data, published as static JSON.'}{' '}
          Every endpoint below is a plain <code>GET</code>. There is no key to request, no quota to
          watch and no rate limit — the files sit on a CDN, so a request costs the publisher
          nothing and costs the upstream exchanges nothing at all.
        </p>

        <div className="url-bar">
          <span className="method">GET</span>
          <code>{base}/…</code>
          <Copy text={base} label="Copy base" />
        </div>

        <dl className="kv">
          <dt>Version</dt>
          <dd>{catalog?.version || '—'}</dd>
          <dt>Published</dt>
          <dd>{moment(catalog?.published_at)}</dd>
          <dt>Records</dt>
          <dd>
            {Object.entries(catalog?.datasets || {})
              .map(([name, count]) => `${int(count)} ${name.split('.')[1]}`)
              .join(' · ') || '—'}
          </dd>
          <dt>Transport</dt>
          <dd>{catalog?.transport || 'Static JSON over HTTPS.'}</dd>
          <dt>Mode</dt>
          <dd>
            {IS_LIVE
              ? `Live FastAPI service at ${API_BASE}`
              : 'Static files published by GitHub Actions to GitHub Pages'}
          </dd>
        </dl>
      </section>

      <Card
        icon={FiTerminal}
        title="Quickstart"
        subtitle="Four ways to pull the same data"
        actions={
          <Segmented options={LANGUAGES} value={language} onChange={setLanguage} label="Language" />
        }
      >
        <CodeBlock code={samples[language]} />
      </Card>

      <Card icon={FiClock} title="Publication schedule" subtitle="When each dataset is refreshed">
        <div className="table-wrap" style={{ maxHeight: 'none' }}>
          <table className="fields">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Cadence</th>
                <th className="hide-sm">Cron (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(schedules).map(([name, spec]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{spec.human}</td>
                  <td className="hide-sm">
                    <code>{spec.cron}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ marginTop: 14, display: 'grid', gap: 10 }}>
          <Callout icon={FiInfo}>
            <strong>Why so infrequent?</strong> PSX publishes one closing board per trading day and
            MUFAP strikes NAV once per business day. Polling either of them faster cannot make the
            number newer — it only puts load on someone else's server. The MUFAP sweep additionally
            stops itself once the published validity date advances, so a normal evening costs one
            or two requests rather than seven.
          </Callout>
          <Callout icon={FiClock} variant="warn">
            <strong>Scheduled runs can drift.</strong> GitHub queues cron-triggered workflows during
            busy periods, so a run may land several minutes late — and a public repository with no
            activity for 60 days has its schedules disabled altogether. Read{' '}
            <code>freshness.fetched_at</code> rather than assuming the clock.
          </Callout>
        </div>
      </Card>

      <Card
        icon={FiShield}
        title="The freshness contract"
        subtitle="Every response carries one of these"
      >
        <p style={{ fontSize: 12.5, color: 'var(--ink-2)', marginBottom: 12, maxWidth: '78ch' }}>
          A consumer should never have to infer staleness from a timestamp. Each response embeds a{' '}
          <code>freshness</code> object that states plainly whether the data is current, overdue,
          degraded or missing — and <code>data_as_of</code> is the source's own date, which is not
          the same thing as when this service fetched it.
        </p>
        <table className="fields">
          <thead>
            <tr>
              <th>Field</th>
              <th>Meaning</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(catalog?.freshness_fields || {}).map(([field, description]) => (
              <tr key={field}>
                <td>{field}</td>
                <td>{description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card
        icon={FiDatabase}
        title="Endpoints"
        subtitle={`${endpoints.length} published paths`}
        flush
      >
        <div style={{ padding: 14 }}>
          {endpoints.map((endpoint) => (
            <Endpoint
              key={endpoint.path}
              endpoint={endpoint}
              base={base}
              schemas={catalog?.schemas || {}}
            />
          ))}
        </div>
      </Card>

      <Card icon={FiServer} title="Running it yourself" subtitle="The same code, as a live service">
        <p style={{ fontSize: 12.5, color: 'var(--ink-2)', maxWidth: '78ch', marginBottom: 12 }}>
          The static files above are generated from the same pipeline that backs the HTTP service —
          identical parsers, identical validation gate, identical envelope. Run it as a server when
          you want query parameters, filtering and pagination handled for you rather than in your
          own code.
        </p>
        <CodeBlock
          code={`git clone ${REPO_URL}.git
cd PSX-MUFAP-MicroService

# One-off scrape into ./data, then build the static API locally
pip install -r requirements-scrape.txt
python scripts/scrape.py --domain psx
python scripts/scrape.py --domain mufap
python scripts/build_static_api.py --data data --out site/api

# Or the full live service, with OpenAPI docs at /docs
pip install -r requirements.txt
uvicorn app.main:app --reload`}
        />
        <div style={{ marginTop: 12 }}>
          <a className="btn" href={REPO_URL} target="_blank" rel="noreferrer noopener">
            <FiGithub aria-hidden="true" />
            Source on GitHub
            <FiExternalLink aria-hidden="true" />
          </a>
        </div>
      </Card>

      <Card icon={FiBook} title="Attribution and terms">
        <p style={{ fontSize: 12.5, color: 'var(--ink-2)', maxWidth: '78ch' }}>
          Data originates from the Pakistan Stock Exchange (<code>dps.psx.com.pk</code>) and the
          Mutual Funds Association of Pakistan (<code>mufap.com.pk</code>), fetched within what each
          site's <code>robots.txt</code> permits. This service reformats and republishes it; it does
          not own it, does not warrant it, and is not investment advice. Verify anything you intend
          to trade on against the source.
        </p>
      </Card>
    </div>
  )
}

function Endpoint({ endpoint, base, schemas }) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState(null)
  const url = `${base}/${concrete(endpoint.path)}`
  const fields = schemas[endpoint.schema]

  const tryIt = async () => {
    setPreview({ status: 'loading' })
    try {
      const response = await fetch(url, { headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(`The server answered ${response.status}.`)
      const body = await response.json()
      // Show the envelope in full and only the first row of data: the point is
      // the shape, and 1,000 rows of it helps nobody.
      const shaped = Array.isArray(body.data)
        ? { ...body, data: body.data.slice(0, 1) }
        : body
      const text = JSON.stringify(shaped, null, 2)
      setPreview({
        status: 'ready',
        text: text.length > 2600 ? `${text.slice(0, 2600)}\n  … truncated` : text,
        rows: Array.isArray(body.data) ? body.data.length : null,
      })
    } catch (error) {
      setPreview({ status: 'error', text: error.message })
    }
  }

  return (
    <article className={open ? 'endpoint is-open' : 'endpoint'}>
      <button
        type="button"
        className="endpoint-head"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <FiChevronRight className="endpoint-chevron" aria-hidden="true" />
        <span className="method">GET</span>
        <span className="endpoint-path">/{endpoint.path}</span>
        <span className="endpoint-summary">{endpoint.summary}</span>
      </button>

      {open && (
        <div className="endpoint-body">
          <p>{endpoint.description}</p>

          <div className="url-bar">
            <code>{url}</code>
            <Copy text={url} label="Copy" />
          </div>

          <div className="toolbar">
            <button type="button" className="btn btn--sm btn--primary" onClick={tryIt}>
              <FiPlay aria-hidden="true" />
              Try it
            </button>
            <a className="btn btn--sm" href={url} target="_blank" rel="noreferrer noopener">
              <FiExternalLink aria-hidden="true" />
              Open raw
            </a>
            {endpoint.records != null && (
              <span className="muted" style={{ fontSize: 12 }}>
                {int(endpoint.records)} records
              </span>
            )}
          </div>

          {preview?.status === 'loading' && <Loading rows={3} />}
          {preview?.status === 'error' && (
            <Callout variant="warn">Could not fetch this endpoint. {preview.text}</Callout>
          )}
          {preview?.status === 'ready' && (
            <>
              <p className="muted" style={{ fontSize: 12 }}>
                <FiCode
                  aria-hidden="true"
                  style={{ verticalAlign: '-2px', marginRight: 5, width: 13, height: 13 }}
                />
                Response shape
                {preview.rows != null ? ` — ${int(preview.rows)} rows, first one shown` : ''}
              </p>
              <CodeBlock code={preview.text} />
            </>
          )}

          {fields && (
            <table className="fields">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(fields).map(([field, description]) => (
                  <tr key={field}>
                    <td>{field}</td>
                    <td>{description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </article>
  )
}
