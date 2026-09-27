# Dashboard — per-page state and what is left

Written 2026-09-27, during the rebuild of the UI onto a dark, minimal shell.

The theme toggle is gone: the dashboard is dark only, `data-theme="dark"` is
fixed in `index.html`, and there is no light stylesheet to drift out of sync.
Every page is built from the same four primitives in `components/ui.jsx` —
`Panel`, `Kpi`, `Tag`, `Note` — so a change to one lands everywhere.

Two rules hold across every page, because breaking either is what made the old
build look untrustworthy:

1. **Four KPI tiles, never more.** `.grid--kpi` is a fixed four-column grid. A
   fifth tile wraps and orphans; a seventh was what made the old header read as
   a wall. If a page needs a fifth number, it belongs in a panel, not a tile.
2. **A zero that means "not published" renders as `—`.** PSX publishes
   `market_cap: 0` for 76 of 747 instruments and MUFAP publishes
   `offer_price: 0.0` for 36 of 553 funds. Neither is a quantity. A zero that
   *is* a quantity — a fund charging no front-end load — stays `0.00%`.

---

## Landing (`pages/Landing.jsx`)

**Done.** Hero, four live figures, four feature cards, a copyable `curl`, and a
freshness line. Every number is read from the live API, so nothing on it can
go stale against the data it is describing.

- [ ] The four feature cards are static prose. If the endpoint list grows, they
      will quietly describe an older service — consider generating the counts
      from `index.json` the way the hero figures already are.
- [ ] No `<meta name="description">` or Open Graph tags. A link to this page
      pasted anywhere unfurls as a bare URL.

## Overview (`pages/Overview.jsx`)

**Done.** Four KPIs, the freshness strip for both sources, a six-card index
board, gainers and losers, and sector activity.

The movers board ranks **only instruments carrying this session's own quote**.
The screener reports a change for all 747 listed instruments, but for one that
did not trade that change is left over from whenever it last did — which is how
the board came to be topped by `GTECHBR` at −93.55% and `FLYNGR1` at −88.89%,
rights letters trading at one and two paisa on no volume. The panel subtitle
names the count it ranked among rather than leaving the reader to infer it.

- [ ] That set is the 120 largest instruments by market cap, because that is
      what the quote budget buys. A genuine mid-cap mover cannot appear. Raising
      `PSX_DETAIL_BUDGET` widens it at a linear cost in requests per run.
- [ ] Sector activity is ordered by combined market cap, so the same eight
      sectors show every day. A "most active today" ordering would say more.

## Stocks (`pages/Stocks.jsx`)

**Done.** Four KPIs, search, sector filter, five preset views (all, gainers,
losers, most traded, full quote), sortable columns, 50 rows a page.

"Average move" reads `avg_change_pct_quoted`, not `avg_change_pct`. The two
disagreed on direction on 2026-09-27 — −1.33% across the universe against
+0.40% across the quoted set, on a day KSE100 closed +0.15%. The universe figure
is still published for consumers who want it, with the caveat in its field doc.

- [ ] Sorting a column sorts the whole filtered set, but `has_quote: false` rows
      carry nulls for volume and OHLC. They sort last, which is right, but there
      is no visual mark on the row saying the blank is "not fetched" rather than
      "zero". A `Tag` on the symbol cell would close that.
- [ ] No column chooser. Nine columns is the most that fits 1440px; below `md`
      four of them hide by breakpoint rather than by choice.

## Indices (`pages/Indices.jsx`)

**Done.** Four breadth KPIs, card and table layouts, search.

- [ ] Cards and table duplicate the row rendering. One should feed the other.
- [ ] Index constituents are published (`psx/indices/{name}.json`, up to 244 KiB
      for ALLSHR) but nothing on this page links to them.

## Mutual funds (`pages/Funds.jsx`)

**Done.** Four KPIs, search, category and AMC filters, five return periods,
sortable, 50 rows a page. Each row states the basis its returns are quoted on.

"Best YTD" and "Mean YTD" report the **absolute** basis and say so. MUFAP quotes
money-market and fixed-return plans on an *annualized* basis (354 funds) and the
rest on an *absolute* one (199), and averaging the two together is not an average
of anything: it produced a headline best of +98.08% from a three-month
fixed-return plan, and a mean of +5.27% that hid absolute-basis funds averaging
−3.01%. `ytd_return_by_basis` publishes both sides; `ytd_return` keeps the old
blended shape with `mixed_basis: true` on it.

- [ ] Sorting by return still ranks both bases in one list. Every row labels its
      basis, but the ordering is apples to oranges. Either split the table or
      default the sort within a basis.
- [ ] 553 funds, no virtualisation. Paging at 50 keeps it responsive; a "show
      all" would not.

## API reference (`pages/ApiDocs.jsx`)

**Done.** Endpoint catalogue read live from `index.json`, field tables, the
freshness contract, copyable samples.

This page rendered **blank** until 2026-09-27: `ui.jsx` renamed `CodeBlock` to
`Code` and the reference was missed. Rollup compiles `<CodeBlock />` to a free
variable and treats it as a global, so the bundle built clean and only this page
threw, behind a `status === 'ready'` branch. Two tests now cover it — see below.

- [ ] Samples are `curl` only. A `fetch` and a `requests` tab would suit the
      consumers this API is actually for.
- [ ] Nothing verifies a documented path still resolves. A renamed file would
      leave the reference pointing at a 404.

## Refresh panel (`components/RefreshPanel.jsx`)

**Done.** Keyless. A button posts to the Worker, which dispatches to Actions;
the panel polls published freshness and reports progress with counts. One abort
controller owns every timer and fetch, so closing it mid-run leaves nothing
behind.

- [ ] The burst-collapse fix (`min_interval_minutes: 5` on the dispatch path)
      is **not end-to-end verified** — the retest hit the Worker's own throttle
      first. It needs one clean run from a cold throttle.
- [ ] Progress counts come from the previous run's record count, so the bar is
      an estimate. It is honest about being one, but a live count would be
      better.

---

## Tests added with this work

`npm test` — 44 tests, run by `ci.yml` before the build.

- `src/unresolved.test.js` — every `<Component>` a source file renders resolves
  to an import or a local definition. This is the check that would have caught
  both `IconFetched` and `CodeBlock`, neither of which any build failed on.
- `src/pages/render.test.jsx` — every page and the refresh panel render against
  trimmed copies of the real published files in `__fixtures__`. The fixtures
  deliberately keep the rows that have broken something: instruments with and
  without a quote, funds on both return bases, and a fund whose prices are a raw
  `0.0` as `app/` still serves them.

Both were verified by reintroducing the bugs and watching them fail.
