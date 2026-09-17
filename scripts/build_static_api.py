#!/usr/bin/env python3
"""Turn the committed snapshots into the static API that GitHub Pages serves.

GitHub Pages serves files, not processes. Every read endpoint of the live
service is a pure function of one snapshot, so each one can be precomputed into
a JSON file at publish time and served from a CDN with no server at all. What a
query parameter selected on the live API is selected here by path:

    GET /api/psx/stocks/gainers?limit=50   ->   api/psx/stocks/gainers.json

Response bodies keep the live service's envelope — `count`, `total`,
`freshness`, `data` — so a consumer written against one works against the
other. The one honest difference is `freshness.age_seconds`, which is measured
at publish time and cannot tick on a static host; `fetched_at` and
`stale_after_seconds` are published alongside it so a consumer recomputes the
true age itself. The dashboard does exactly that.

Deliberately standard-library only. This step runs in the deploy job, which
therefore installs no Python packages at all.

    python scripts/build_static_api.py --data data --out site/api
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

PKT = timezone(timedelta(hours=5), name="PKT")
UTC = timezone.utc

# The published cadence, in UTC, exactly as the workflow cron expresses it.
# Keeping one definition means the freshness contract and the schedule can
# never drift apart: `next_refresh_at` is derived from these, not written down
# a second time.
SCHEDULES: dict[str, dict[str, Any]] = {
    "psx": {
        "cron": "10 12 * * 1-5",
        "weekdays": [0, 1, 2, 3, 4],        # Mon-Fri, UTC (JSON-serialisable)
        "hours": [12],
        "minute": 10,
        "human": "Once per working day, 17:10 PKT — after the PSX close.",
        "grace_minutes": 180,
    },
    "mufap": {
        "cron": "5 13-19 * * 1-5",
        "weekdays": [0, 1, 2, 3, 4],        # Mon-Fri, UTC (JSON-serialisable)
        "hours": list(range(13, 20)),       # 18:05 -> 00:05 PKT
        "minute": 5,
        "human": "Hourly on working evenings, 18:05 to 00:05 PKT.",
        "grace_minutes": 90,
    },
}

RETURN_PERIODS = ("ytd", "mtd", "d1", "d15", "d30", "d90",
                  "d180", "d270", "d365", "y2", "y3")

MOVERS_LIMIT = 50
TOP_LIMIT = 50


# ── schedule ──────────────────────────────────────────────────────────────────

def next_run_after(moment: datetime, domain: str) -> datetime:
    """The next scheduled run strictly after `moment`, in UTC.

    Weekend-aware by construction, which is the point: on a Saturday the PSX
    snapshot is two days old and still the newest close that exists. Measuring
    staleness against the next scheduled run rather than a fixed age keeps the
    dashboard from labelling a correct weekend snapshot as stale.
    """
    spec = SCHEDULES[domain]
    cursor = moment.astimezone(UTC)
    for day in range(0, 9):                       # a long weekend is at most 4
        candidate_day = (cursor + timedelta(days=day)).date()
        if candidate_day.weekday() not in spec["weekdays"]:
            continue
        for hour in spec["hours"]:
            candidate = datetime(candidate_day.year, candidate_day.month,
                                 candidate_day.day, hour, spec["minute"],
                                 tzinfo=UTC)
            # Strictly after, not at-or-after. A snapshot fetched at exactly
            # 12:10:00 would otherwise take the run that produced it as the
            # next one due, and be called stale three hours later.
            if candidate > cursor:
                return candidate
    # Unreachable for the schedules above; a sane fallback beats an exception.
    return cursor + timedelta(days=1)


def parse_moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=PKT)


# ── snapshots ─────────────────────────────────────────────────────────────────

class Dataset:
    """One snapshot file, plus everything the envelope needs to describe it."""

    def __init__(self, name: str, domain: str, path: Path, now: datetime) -> None:
        self.name = name
        self.domain = domain
        self.now = now
        raw: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"  ! {path.name}: unreadable ({exc}) — treated as missing")
        self.rows: list[dict[str, Any]] = raw.get("rows") or []
        self.meta: dict[str, Any] = raw.get("meta") or {}
        self.fetched_at: str | None = raw.get("fetched_at") or None
        self.data_as_of: str | None = raw.get("data_as_of")
        self.error: str | None = raw.get("error")

    @property
    def count(self) -> int:
        return len(self.rows)

    def freshness(self, market_status: str | None = None) -> dict[str, Any]:
        fetched = parse_moment(self.fetched_at)
        due = next_run_after(fetched or self.now, self.domain)
        grace = timedelta(minutes=SCHEDULES[self.domain]["grace_minutes"])
        deadline = due + grace

        age = (self.now - fetched).total_seconds() if fetched else None
        stale_after = int((deadline - fetched).total_seconds()) if fetched else None

        if not self.rows:
            state = "unavailable"
        elif self.error:
            state = "degraded"
        elif self.now > deadline:
            state = "stale"
        else:
            state = "fresh"

        envelope: dict[str, Any] = {
            "state": state,
            "data_as_of": self.data_as_of,
            "fetched_at": self.fetched_at,
            "published_at": self.now.astimezone(PKT).isoformat(timespec="seconds"),
            # Measured at publish time. A static file cannot tick, so recompute
            # from `fetched_at` if you need the age right now.
            "age_seconds": round(age, 1) if age is not None else None,
            "stale_after_seconds": stale_after,
            "next_refresh_at": due.astimezone(PKT).isoformat(timespec="seconds"),
            "record_count": self.count,
            "schedule": SCHEDULES[self.domain]["human"],
        }
        if market_status is not None:
            envelope["market_status"] = market_status
        if self.error:
            envelope["error"] = self.error
        return envelope


# ── writing ───────────────────────────────────────────────────────────────────

class Writer:
    def __init__(self, api_root: Path, site_root: Path) -> None:
        self.api_root = api_root
        self.site_root = site_root
        self.written: list[tuple[str, int]] = []

    def write(self, relative: str, payload: Any) -> None:
        self._emit(self.api_root / relative, f"api/{relative}", payload)

    def write_site(self, relative: str, payload: Any) -> None:
        """Write next to the dashboard rather than inside the API tree."""
        self._emit(self.site_root / relative, relative, payload)

    def _emit(self, target: Path, relative: str, payload: Any) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Compact separators only: every byte here is served on every page load,
        # and a pretty-printed 1,000-row table is roughly a third larger.
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
        target.write_bytes(body)
        self.written.append((relative, len(body)))

    @property
    def total_bytes(self) -> int:
        return sum(size for _, size in self.written)


def envelope(rows: list[dict[str, Any]], dataset: Dataset, *,
             total: int | None = None, limit: int | None = None,
             market_status: str | None = None,
             extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "count": len(rows),
        "total_filtered": total if total is not None else len(rows),
        "total": dataset.count,
        "offset": 0,
        "limit": limit,
        "freshness": dataset.freshness(market_status),
        "data": rows,
    }
    if extra:
        payload.update(extra)
    return payload


def rounded(value: Any, places: int) -> Any:
    return None if value is None else round(value, places)


def shape_stock(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for field in ("ldcp", "open", "high", "low", "current", "change", "change_pct"):
        out[field] = rounded(out.get(field), 2)
    return out


def shape_index(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for field in ("value", "current", "high", "low", "change", "change_pct"):
        out[field] = rounded(out.get(field), 2)
    return out


def shape_fund(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for field in ("nav", "offer_price", "repurchase_price", "market_price",
                  "front_end_load", "back_end_load", "contingent_load"):
        out[field] = rounded(out.get(field), 4)
    out["returns"] = {k: rounded(v, 2) for k, v in (out.get("returns") or {}).items()}
    return out


def ordered_pool(rows: Iterable[dict[str, Any]], key,
                 reverse: bool = True) -> list[dict[str, Any]]:
    """Everything the key ranks, in rank order. The caller takes the page."""
    pool = [r for r in rows if key(r) is not None]
    pool.sort(key=key, reverse=reverse)
    return pool


# ── PSX ───────────────────────────────────────────────────────────────────────

def build_psx(writer: Writer, stocks: Dataset, indices: Dataset) -> list[dict[str, Any]]:
    market_status = (stocks.meta or {}).get("market_status")
    summary = (stocks.meta or {}).get("summary") or {}
    shaped = [shape_stock(r) for r in stocks.rows]
    traded = [r for r in shaped if r.get("traded")]
    catalog: list[dict[str, Any]] = []

    shaped.sort(key=lambda r: r.get("volume") or 0, reverse=True)
    writer.write("psx/stocks.json", envelope(shaped, stocks, market_status=market_status))
    catalog.append(entry("psx/stocks.json", "Every listed instrument",
                         "The full PSX universe — around a thousand instruments. Those "
                         "that traded in the session carry prices and `traded: true`; "
                         "the rest carry nulls, so \"did not trade\" is distinguishable "
                         "from \"we failed to fetch it\". Sorted by volume, descending.",
                         records=len(shaped), schema="stock"))

    writer.write("psx/stocks/summary.json",
                 {**summary, "freshness": stocks.freshness(market_status)})
    catalog.append(entry("psx/stocks/summary.json", "Market breadth and totals",
                         "Advancers, decliners, traded volume and traded value for the "
                         "session, plus the average percentage move.",
                         records=None, schema="summary"))

    for name, label, key, reverse, description in (
        ("gainers", "Top gainers",
         lambda r: r["change_pct"] if r.get("traded") and (r.get("change_pct") or 0) > 0 else None,
         True, "Instruments that closed up, ordered by percentage gain."),
        ("losers", "Top losers",
         lambda r: r["change_pct"] if r.get("traded") and (r.get("change_pct") or 0) < 0 else None,
         False, "Instruments that closed down, ordered by percentage loss."),
        ("active", "Most active by volume",
         lambda r: r.get("volume") if r.get("traded") else None,
         True, "Instruments ordered by shares traded in the session."),
    ):
        pool = ordered_pool(traded, key, reverse=reverse)
        page = pool[:MOVERS_LIMIT]
        writer.write(f"psx/stocks/{name}.json",
                     envelope(page, stocks, total=len(pool), limit=MOVERS_LIMIT,
                              market_status=market_status))
        catalog.append(entry(f"psx/stocks/{name}.json", label, description,
                             records=len(page), schema="stock"))

    sectors = sector_breadth(traded)
    writer.write("psx/sectors.json", envelope(sectors, stocks, market_status=market_status))
    catalog.append(entry("psx/sectors.json", "Sector breadth",
                         "Per-sector counts, advancers and decliners, traded volume and "
                         "the average move — derived from the traded instruments.",
                         records=len(sectors), schema="sector"))

    index_rows = [shape_index(r) for r in indices.rows]
    writer.write("psx/indices.json", envelope(index_rows, indices))
    catalog.append(entry("psx/indices.json", "Index board",
                         "KSE100, KSE30, KMI30 and the rest of the board, with the "
                         "session high, low and change for each.",
                         records=len(index_rows), schema="index"))

    writer.write("psx/market-status.json", {
        "status": market_status or "unknown",
        "last_tick": stocks.data_as_of,
        "freshness": stocks.freshness(market_status),
    })
    catalog.append(entry("psx/market-status.json", "Trading status",
                         "Whether PSX was trading when the snapshot was taken, inferred "
                         "from the newest tick in the KSE100 intraday series rather than "
                         "from a hardcoded calendar.",
                         records=None, schema="market_status"))

    writer.write("psx/index.json", {
        "service": "PSX Stock Exchange",
        "source": "https://dps.psx.com.pk",
        "schedule": SCHEDULES["psx"],
        "datasets": {"stocks": stocks.count, "indices": indices.count},
        "freshness": stocks.freshness(market_status),
    })
    return catalog


def sector_breadth(traded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in traded:
        name = row.get("sector") or "Unclassified"
        bucket = buckets.setdefault(name, {"sector": name, "traded": 0, "gainers": 0,
                                           "losers": 0, "unchanged": 0, "volume": 0,
                                           "_changes": []})
        bucket["traded"] += 1
        bucket["volume"] += row.get("volume") or 0
        change = row.get("change") or 0
        bucket["gainers" if change > 0 else "losers" if change < 0 else "unchanged"] += 1
        if row.get("change_pct") is not None:
            bucket["_changes"].append(row["change_pct"])

    out = []
    for bucket in buckets.values():
        changes = bucket.pop("_changes")
        bucket["avg_change_pct"] = round(sum(changes) / len(changes), 2) if changes else None
        out.append(bucket)
    out.sort(key=lambda b: b["volume"], reverse=True)
    return out


# ── MUFAP ─────────────────────────────────────────────────────────────────────

def build_mufap(writer: Writer, funds: Dataset) -> list[dict[str, Any]]:
    shaped = [shape_fund(r) for r in funds.rows]
    meta = funds.meta or {}
    catalog: list[dict[str, Any]] = []

    shaped.sort(key=lambda r: r.get("fund_name") or "")
    writer.write("mufap/funds.json", envelope(shaped, funds))
    catalog.append(entry("mufap/funds.json", "Every fund",
                         "The whole MUFAP daily statistics table: NAV, offer and "
                         "repurchase prices, sales loads, rating, trustee, AMC and "
                         "eleven return periods per fund. Sorted by name.",
                         records=len(shaped), schema="fund"))

    categories = meta.get("categories") or []
    writer.write("mufap/funds/categories.json", envelope(categories, funds))
    catalog.append(entry("mufap/funds/categories.json", "Categories",
                         "Every fund category with the number of funds in it.",
                         records=len(categories), schema="category"))

    amcs: dict[str, int] = {}
    for row in shaped:
        if row.get("amc"):
            amcs[row["amc"]] = amcs.get(row["amc"], 0) + 1
    amc_rows = [{"amc": k, "count": v} for k, v in sorted(amcs.items())]
    writer.write("mufap/funds/amcs.json", envelope(amc_rows, funds))
    catalog.append(entry("mufap/funds/amcs.json", "Asset management companies",
                         "Every AMC with the number of funds it manages.",
                         records=len(amc_rows), schema="amc"))

    writer.write("mufap/funds/stats.json",
                 {**(meta.get("stats") or {}), "category_filter": None,
                  "freshness": funds.freshness()})
    catalog.append(entry("mufap/funds/stats.json", "Aggregate statistics",
                         "Fund and category counts, and the mean, median, minimum and "
                         "maximum NAV and year-to-date return across the industry.",
                         records=None, schema="stats"))

    for period in RETURN_PERIODS:
        pool = ordered_pool(shaped, lambda r, p=period: (r.get("returns") or {}).get(p))
        writer.write(f"mufap/funds/top/{period}.json",
                     envelope(pool[:TOP_LIMIT], funds, total=len(pool), limit=TOP_LIMIT,
                              extra={"period": period}))
    catalog.append(entry("mufap/funds/top/{period}.json", "Best performers",
                         "The strongest funds over one return period. `{period}` is one "
                         f"of: {', '.join(RETURN_PERIODS)}.",
                         records=TOP_LIMIT, schema="fund"))

    writer.write("mufap/index.json", {
        "service": "MUFAP Mutual Funds",
        "source": "https://www.mufap.com.pk",
        "schedule": SCHEDULES["mufap"],
        "funds": funds.count,
        "categories": len(categories),
        "amcs": meta.get("amc_count"),
        "freshness": funds.freshness(),
    })
    return catalog


# ── catalog ───────────────────────────────────────────────────────────────────

def entry(path: str, summary: str, description: str, *,
          records: int | None, schema: str) -> dict[str, Any]:
    return {"path": path, "summary": summary, "description": description,
            "records": records, "schema": schema}


SCHEMAS: dict[str, dict[str, str]] = {
    "stock": {
        "symbol": "Ticker as PSX lists it, e.g. `OGDC`.",
        "name": "Registered company name.",
        "sector": "PSX sector classification.",
        "ldcp": "Last day closing price.",
        "open": "Session opening price.",
        "high": "Session high.",
        "low": "Session low.",
        "current": "Last traded price. Falls back to LDCP for an instrument that did not trade.",
        "change": "Absolute move against LDCP.",
        "change_pct": "Percentage move against LDCP.",
        "volume": "Shares traded in the session.",
        "traded": "`false` means the instrument is listed but did not trade — not that the fetch failed.",
        "is_etf": "Exchange-traded fund.",
        "is_debt": "Debt instrument rather than equity.",
    },
    "index": {
        "index_name": "Index name, e.g. `KSE100`.",
        "current": "Current index level.",
        "high": "Session high.",
        "low": "Session low.",
        "change": "Absolute move.",
        "change_pct": "Percentage move.",
    },
    "fund": {
        "fund_name": "Fund name exactly as MUFAP publishes it.",
        "category": "MUFAP category, e.g. `Money Market`, `Equity`.",
        "sector": "Conventional or Shariah-compliant.",
        "amc": "Asset management company.",
        "trustee": "Trustee institution.",
        "rating": "Stability or performance rating where one is published.",
        "benchmark": "The fund's stated benchmark.",
        "nav": "Net asset value per unit, to four decimals.",
        "offer_price": "Price to buy a unit.",
        "repurchase_price": "Price at which the fund buys a unit back.",
        "front_end_load": "Sales load charged on purchase, in percent.",
        "back_end_load": "Load charged on redemption, in percent.",
        "contingent_load": "Contingent load where one applies.",
        "inception_date": "Fund launch date.",
        "validity_date": "The date this NAV is valid for. Funds do not all publish on the same day.",
        "return_basis": "How the fund reports returns — annualised or absolute.",
        "returns": "Object keyed by period: " + ", ".join(f"`{p}`" for p in RETURN_PERIODS) + ".",
    },
    "sector": {
        "sector": "Sector name.",
        "traded": "Instruments in the sector that traded.",
        "gainers": "How many closed up.",
        "losers": "How many closed down.",
        "unchanged": "How many closed flat.",
        "volume": "Combined shares traded.",
        "avg_change_pct": "Mean percentage move across the sector.",
    },
    "category": {"category": "Category name.", "count": "Funds in it."},
    "amc": {"amc": "Company name.", "count": "Funds it manages."},
    "summary": {
        "listed_instruments": "Instruments listed on PSX.",
        "traded_instruments": "How many of them traded.",
        "gainers": "Advancers.", "losers": "Decliners.", "unchanged": "Flat.",
        "total_volume": "Shares traded across the market.",
        "total_traded_value": "Traded value in PKR.",
        "avg_change_pct": "Mean percentage move.",
    },
    "stats": {
        "total_funds": "Funds in the snapshot.",
        "total_categories": "Distinct categories.",
        "nav": "Mean, median, minimum and maximum NAV.",
        "ytd_return": "Mean, best and worst year-to-date return, and how many funds reported one.",
    },
    "market_status": {
        "status": "`open`, `closed` or `unknown`.",
        "last_tick": "Timestamp of the newest tick seen in the KSE100 intraday series.",
    },
}

FRESHNESS_FIELDS = {
    "state": "`fresh`, `stale`, `degraded` or `unavailable`.",
    "data_as_of": "What the source says the data is from — the last trade timestamp for PSX, the NAV validity date for MUFAP. This is the number that matters.",
    "fetched_at": "When this service fetched it.",
    "published_at": "When this file was built.",
    "age_seconds": "Age at publish time. A static file cannot tick — recompute from `fetched_at` for the age right now.",
    "stale_after_seconds": "Age, measured from `fetched_at`, past which the data should be treated as stale. Derived from the schedule, so it already accounts for weekends.",
    "next_refresh_at": "When the next scheduled run is due, in PKT.",
    "record_count": "Rows in the underlying snapshot.",
    "error": "Present only when the last refresh failed. The data shown is the previous good snapshot.",
}


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="data", help="directory of snapshot JSON")
    parser.add_argument("--out", default="site/api", help="directory to write the API into")
    parser.add_argument("--site-out", default="",
                        help="site root for /health.json and /ready.json "
                             "(default: the API directory's parent)")
    parser.add_argument("--base-url", default=os.getenv("API_BASE_URL", ""),
                        help="public URL the API is served from, recorded in index.json")
    args = parser.parse_args()

    data_dir = Path(args.data)
    out_dir = Path(args.out)
    now = datetime.now(PKT)

    print(f"building static API from {data_dir}/ into {out_dir}/")
    writer = Writer(out_dir, Path(args.site_out) if args.site_out else out_dir.parent)

    # One dataset is read, shaped and written before the next is opened. The
    # peak is therefore one dataset, not all of them.
    stocks = Dataset("psx.stocks", "psx", data_dir / "psx.stocks.json", now)
    indices = Dataset("psx.indices", "psx", data_dir / "psx.indices.json", now)
    psx_catalog = build_psx(writer, stocks, indices)
    psx_state = {"stocks": stocks.freshness((stocks.meta or {}).get("market_status")),
                 "indices": indices.freshness()}
    stocks_count, indices_count = stocks.count, indices.count
    del stocks, indices

    funds = Dataset("mufap.funds", "mufap", data_dir / "mufap.funds.json", now)
    mufap_catalog = build_mufap(writer, funds)
    mufap_state = {"funds": funds.freshness()}
    funds_count = funds.count
    del funds

    ready = bool(stocks_count or funds_count)
    writer.write_site("health.json", {"status": "ok",
                                      "published_at": now.isoformat(timespec="seconds")})
    writer.write_site("ready.json", {
        "status": "ready" if ready else "warming_up",
        "datasets": {"psx": psx_state, "mufap": mufap_state},
        "time": now.isoformat(timespec="seconds"),
    })

    writer.write("index.json", {
        "service": "PK Finance Unified Service",
        "description": "Pakistan Stock Exchange and MUFAP mutual fund data, refreshed "
                       "on a schedule by GitHub Actions and served as static JSON from "
                       "GitHub Pages.",
        "version": read_version(),
        "base_url": args.base_url.rstrip("/"),
        "published_at": now.isoformat(timespec="seconds"),
        "transport": "Static JSON over HTTPS. GET only, no authentication, CORS open to "
                     "every origin. Nothing is rate limited.",
        "schedules": SCHEDULES,
        "datasets": {"psx.stocks": stocks_count, "psx.indices": indices_count,
                     "mufap.funds": funds_count},
        "freshness_fields": FRESHNESS_FIELDS,
        "schemas": SCHEMAS,
        "endpoints": [
            entry("index.json", "This catalog",
                  "Every endpoint, every field, and the publication schedule. The "
                  "dashboard's API reference is rendered from this file, so the "
                  "documentation cannot drift from what is actually published.",
                  records=None, schema="catalog"),
            *psx_catalog,
            *mufap_catalog,
        ],
    })

    total_kb = writer.total_bytes / 1024
    print(f"wrote {len(writer.written)} files, {total_kb:,.0f} KiB total")
    for path, size in sorted(writer.written, key=lambda item: -item[1])[:6]:
        print(f"  {size / 1024:8,.1f} KiB  {path}")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(
                    f"\n### 📦 Static API built\n\n"
                    f"{len(writer.written)} files · {total_kb:,.0f} KiB · "
                    f"{stocks_count:,} stocks · {indices_count} indices · "
                    f"{funds_count:,} funds\n"
                )
        except OSError:
            pass

    if not ready:
        print("! no data in any dataset — the site will render its empty state")
    return 0


def read_version() -> str:
    main_py = Path(__file__).resolve().parent.parent / "app" / "main.py"
    try:
        for line in main_py.read_text(encoding="utf-8").splitlines():
            if line.startswith("VERSION"):
                return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return "0.0.0"


if __name__ == "__main__":
    raise SystemExit(main())
