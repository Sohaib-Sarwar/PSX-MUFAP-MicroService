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
import re
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
        "cron": "0 12 * * 1-5",
        "weekdays": [0, 1, 2, 3, 4],        # Mon-Fri, UTC (JSON-serialisable)
        "hours": [12],
        "minute": 0,
        "human": "Once per working day at 17:00 PKT, after the PSX close. "
                 "Every run fetches and replaces; nothing is skipped.",
        "grace_minutes": 180,
    },
    "mufap": {
        "cron": "0 13-19 * * 1-5",
        "weekdays": [0, 1, 2, 3, 4],        # Mon-Fri, UTC (JSON-serialisable)
        "hours": list(range(13, 20)),       # 18:00 -> 00:00 PKT
        "minute": 0,
        "human": "Hourly on working evenings at 18:00, 19:00, 20:00, 21:00, "
                 "22:00, 23:00 and 00:00 PKT. Every run fetches and replaces.",
        "grace_minutes": 90,
    },
}

RETURN_PERIODS = ("ytd", "mtd", "d1", "d15", "d30", "d90",
                  "d180", "d270", "d365", "y2", "y3")

MOVERS_LIMIT = 50
TOP_LIMIT = 50


def slugify(name: str) -> str:
    """A URL path segment for a sector or category name.

    Stable across runs, which matters: these become endpoint paths that other
    projects hardcode. "Shariah Compliant Money Market" -> "shariah-compliant-
    money-market", and it stays that way as long as MUFAP keeps the name.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "other").lower()).strip("-")
    return cleaned or "other"


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


def single(record: dict[str, Any], dataset: Dataset, *,
           market_status: str | None = None) -> dict[str, Any]:
    """One record, carrying the same freshness block a list response does."""
    return {"freshness": dataset.freshness(market_status), "data": record}


def rounded(value: Any, places: int) -> Any:
    return None if value is None else round(value, places)


def shape_stock(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for field in ("ldcp", "open", "high", "low", "current", "change", "change_pct",
                  "change_1y_pct", "pe_ratio", "dividend_yield"):
        out[field] = rounded(out.get(field), 2)
    for field in ("market_cap", "free_float", "volume_30d_avg"):
        value = out.get(field)
        out[field] = None if value is None else round(value)
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

def build_psx(writer: Writer, stocks: Dataset, indices: Dataset,
              session: Dataset) -> list[dict[str, Any]]:
    market_status = (stocks.meta or {}).get("market_status")
    summary = (stocks.meta or {}).get("summary") or {}
    shaped = [shape_stock(r) for r in stocks.rows]
    catalog: list[dict[str, Any]] = []

    shaped.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
    writer.write("psx/stocks.json", envelope(shaped, stocks, market_status=market_status))
    catalog.append(entry("psx/stocks.json", "Every listed instrument",
                         "The full PSX universe with price, percentage move, market "
                         "capitalisation, P/E, dividend yield, free float, 30-day average "
                         "volume and index memberships. Ordered by market cap. `has_quote` "
                         "tells you whether today's OHLC and session volume are present "
                         "for that row.",
                         records=len(shaped), schema="stock"))

    # One file per instrument. A consumer that wants OGDC should fetch 700 bytes,
    # not 300 KB and a scan.
    for row in shaped:
        writer.write(f"psx/stocks/{row['symbol'].lower()}.json",
                     single(row, stocks, market_status=market_status))
    catalog.append(entry("psx/stocks/{symbol}.json", "One instrument",
                         "A single instrument by lower-case ticker, e.g. "
                         "`psx/stocks/ogdc.json`. Around 700 bytes instead of the "
                         f"full {len(shaped)}-row file.",
                         records=1, schema="stock"))

    writer.write("psx/stocks/summary.json",
                 {**summary, "freshness": stocks.freshness(market_status)})
    catalog.append(entry("psx/stocks/summary.json", "Market breadth and totals",
                         "Advancing, declining and unchanged counts, total trades, "
                         "exchange volume and value — taken from the exchange's own "
                         "session header rather than counted from the universe.",
                         records=None, schema="summary"))

    quoted = [r for r in shaped if r.get("has_quote")]
    writer.write("psx/stocks/quoted.json",
                 envelope(quoted, stocks, market_status=market_status))
    catalog.append(entry("psx/stocks/quoted.json", "Instruments with a full quote",
                         "Only the rows carrying today's LDCP, open, high, low and "
                         "session volume. PSX withdrew the bulk quote feed, so these "
                         "are fetched per instrument and the set is bounded.",
                         records=len(quoted), schema="stock"))

    for name, label, key, reverse, description in (
        ("gainers", "Top gainers",
         lambda r: r["change_pct"] if (r.get("change_pct") or 0) > 0 else None,
         True, "Instruments up on the session, by percentage gain."),
        ("losers", "Top losers",
         lambda r: r["change_pct"] if (r.get("change_pct") or 0) < 0 else None,
         False, "Instruments down on the session, by percentage loss."),
        ("active", "Most active by 30-day average volume",
         lambda r: r.get("volume_30d_avg"), True,
         "Ordered by 30-day average volume, which is what the screener publishes "
         "now that per-session volume is no longer served in bulk."),
    ):
        pool = ordered_pool(shaped, key, reverse=reverse)
        page = pool[:MOVERS_LIMIT]
        writer.write(f"psx/stocks/{name}.json",
                     envelope(page, stocks, total=len(pool), limit=MOVERS_LIMIT,
                              market_status=market_status))
        catalog.append(entry(f"psx/stocks/{name}.json", label, description,
                             records=len(page), schema="stock"))

    # Fundamental rankings — only possible since PSX started publishing these.
    for name, label, key, description in (
        ("market-cap", "Largest by market capitalisation",
         lambda r: r.get("market_cap"), "The biggest listed companies by market value."),
        ("dividend-yield", "Highest dividend yield",
         lambda r: r.get("dividend_yield") or None,
         "Highest trailing dividend yield, in percent."),
        ("pe-ratio", "Lowest price/earnings",
         lambda r: -r["pe_ratio"] if (r.get("pe_ratio") or 0) > 0 else None,
         "Lowest positive trailing P/E. Instruments with no earnings are excluded."),
        ("yearly-gainers", "Best twelve months",
         lambda r: r.get("change_1y_pct"), "Largest one-year percentage gain."),
    ):
        pool = ordered_pool(shaped, key)
        page = pool[:MOVERS_LIMIT]
        writer.write(f"psx/stocks/top/{name}.json",
                     envelope(page, stocks, total=len(pool), limit=MOVERS_LIMIT,
                              market_status=market_status))
        catalog.append(entry(f"psx/stocks/top/{name}.json", label, description,
                             records=len(page), schema="stock"))

    sectors = sector_breadth(shaped)
    writer.write("psx/sectors.json", envelope(sectors, stocks, market_status=market_status))
    catalog.append(entry("psx/sectors.json", "Sector breadth",
                         "Per-sector counts, advancers and decliners, combined market "
                         "capitalisation and average move. Each row carries the `slug` "
                         "that addresses its own endpoint below.",
                         records=len(sectors), schema="sector"))

    for sector in sectors:
        members = [r for r in shaped
                   if (r.get("sector") or "Unclassified") == sector["sector"]]
        members.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
        writer.write(f"psx/sectors/{sector['slug']}.json",
                     envelope(members, stocks, market_status=market_status,
                              extra={"sector": sector["sector"]}))
    catalog.append(entry("psx/sectors/{slug}.json", "Instruments in one sector",
                         "Every instrument in a single sector, largest first. `{slug}` "
                         "comes from `psx/sectors.json` — there are "
                         f"{len(sectors)}, e.g. "
                         f"`{sectors[0]['slug'] if sectors else 'commercial-banks'}`.",
                         records=None, schema="stock"))

    # Index membership — the screener publishes it per instrument, so it can be
    # inverted here into the constituent list a consumer actually wants.
    memberships: dict[str, list[dict[str, Any]]] = {}
    for row in shaped:
        for index_name in row.get("indices") or []:
            memberships.setdefault(index_name, []).append(row)
    index_rows = [shape_index(r) for r in indices.rows]
    for index_name, members in memberships.items():
        members.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
        writer.write(f"psx/indices/{slugify(index_name)}.json",
                     envelope(members, stocks, market_status=market_status,
                              extra={"index": index_name}))
    writer.write("psx/indices.json", envelope(index_rows, indices))
    catalog.append(entry("psx/indices.json", "Index board",
                         "KSE100, KSE30, KMI30, ALLSHR and the rest, with session high, "
                         "low and change for each.",
                         records=len(index_rows), schema="index"))
    catalog.append(entry("psx/indices/{name}.json", "Constituents of one index",
                         "Every instrument that belongs to an index, largest first. "
                         f"{len(memberships)} indices are addressable this way, e.g. "
                         "`psx/indices/kse100.json`.",
                         records=None, schema="stock"))

    writer.write("psx/market-status.json", {
        "status": market_status or "unknown",
        "session_at": stocks.data_as_of,
        "segments": session.rows,
        "freshness": stocks.freshness(market_status),
    })
    catalog.append(entry("psx/market-status.json", "Trading status",
                         "Whether PSX was trading when the snapshot was taken, with the "
                         "state of each market segment. Taken from the exchange's own "
                         "panel, not inferred from a clock.",
                         records=None, schema="market_status"))

    writer.write("psx/session.json", {
        **(session.meta or {}),
        "segments": session.rows,
        "freshness": session.freshness(market_status),
    })
    catalog.append(entry("psx/session.json", "Session totals by market segment",
                         "Trades, volume and value for Regular, Futures, Bills & Bonds "
                         "and every other segment, plus the exchange-wide totals and "
                         "the timestamp PSX stamped the board with.",
                         records=len(session.rows), schema="segment"))

    writer.write("psx/index.json", {
        "service": "PSX Stock Exchange",
        "source": "https://dps.psx.com.pk",
        "schedule": SCHEDULES["psx"],
        "datasets": {"stocks": stocks.count, "indices": indices.count,
                     "segments": session.count},
        "freshness": stocks.freshness(market_status),
    })
    return catalog


def sector_breadth(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("sector") or "Unclassified"
        bucket = buckets.setdefault(name, {"sector": name, "slug": slugify(name),
                                           "instruments": 0, "gainers": 0,
                                           "losers": 0, "unchanged": 0,
                                           "market_cap": 0, "_changes": []})
        bucket["instruments"] += 1
        bucket["market_cap"] += row.get("market_cap") or 0
        change = row.get("change_pct") or 0
        bucket["gainers" if change > 0 else "losers" if change < 0 else "unchanged"] += 1
        if row.get("change_pct") is not None:
            bucket["_changes"].append(row["change_pct"])

    out = []
    for bucket in buckets.values():
        changes = bucket.pop("_changes")
        bucket["avg_change_pct"] = round(sum(changes) / len(changes), 2) if changes else None
        out.append(bucket)
    out.sort(key=lambda b: b["market_cap"], reverse=True)
    return out


# ── MUFAP ─────────────────────────────────────────────────────────────────────

def build_mufap(writer: Writer, funds: Dataset
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (catalog, shaped rows). The shaped rows carry the slugs the
    search index needs, and they are the only place those slugs exist."""
    shaped = [shape_fund(r) for r in funds.rows]
    meta = funds.meta or {}
    catalog: list[dict[str, Any]] = []

    shaped.sort(key=lambda r: r.get("fund_name") or "")

    # A fund name is not a URL. Every row carries the slug that addresses its own
    # endpoint, assigned before anything is written so the list and the per-fund
    # files can never disagree about it. A handful of VPS sub-funds share a name,
    # so the second occurrence gets a numeric suffix.
    seen: dict[str, int] = {}
    for row in shaped:
        slug = slugify(row.get("fund_name") or "")
        seen[slug] = seen.get(slug, 0) + 1
        row["slug"] = slug if seen[slug] == 1 else f"{slug}-{seen[slug]}"
    writer.write("mufap/funds.json", envelope(shaped, funds))
    catalog.append(entry("mufap/funds.json", "Every fund",
                         "The whole MUFAP daily statistics table: NAV, offer and "
                         "repurchase prices, sales loads, rating, trustee, AMC and "
                         "eleven return periods per fund. Sorted by name.",
                         records=len(shaped), schema="fund"))

    categories = [{**row, "slug": slugify(row.get("category", ""))}
                  for row in (meta.get("categories") or [])]
    writer.write("mufap/funds/categories.json", envelope(categories, funds))
    catalog.append(entry("mufap/funds/categories.json", "Categories",
                         "Every fund category with the number of funds in it. Each row "
                         "carries the `slug` that addresses its own endpoint below.",
                         records=len(categories), schema="category"))

    # One file per category, for the same reason as the PSX sectors: comparing
    # money market funds should not mean downloading every equity fund too.
    for category in categories:
        members = [f for f in shaped if (f.get("category") or "") == category["category"]]
        members.sort(key=lambda f: (f.get("returns") or {}).get("ytd") is None)
        writer.write(f"mufap/funds/category/{category['slug']}.json",
                     envelope(members, funds, extra={"category": category["category"]}))
    catalog.append(entry("mufap/funds/category/{slug}.json", "Funds in one category",
                         "Every fund in a single category. `{slug}` comes from the `slug` "
                         "field in `mufap/funds/categories.json` — there are "
                         f"{len(categories)} of them, e.g. "
                         f"`{categories[0]['slug'] if categories else 'money-market'}`.",
                         records=None, schema="fund"))

    # One file per fund.
    for row in shaped:
        writer.write(f"mufap/fund/{row['slug']}.json", single(row, funds))
    catalog.append(entry("mufap/fund/{slug}.json", "One fund",
                         "A single fund by slug, e.g. "
                         f"`mufap/fund/{shaped[0]['slug'] if shaped else 'money-market-fund'}.json`. "
                         "The slug is on every "
                         "row of `mufap/funds.json` and of the search index.",
                         records=1, schema="fund"))

    amcs: dict[str, list[dict[str, Any]]] = {}
    for row in shaped:
        if row.get("amc"):
            amcs.setdefault(row["amc"], []).append(row)
    amc_rows = [{"amc": name, "slug": slugify(name), "count": len(members),
                 "categories": sorted({m.get("category") for m in members if m.get("category")})}
                for name, members in sorted(amcs.items())]
    writer.write("mufap/funds/amcs.json", envelope(amc_rows, funds))
    catalog.append(entry("mufap/funds/amcs.json", "Asset management companies",
                         "Every AMC with its fund count, the categories it operates in, "
                         "and the `slug` addressing its own endpoint below.",
                         records=len(amc_rows), schema="amc"))

    for name, members in amcs.items():
        members.sort(key=lambda f: f.get("fund_name") or "")
        writer.write(f"mufap/funds/amc/{slugify(name)}.json",
                     envelope(members, funds, extra={"amc": name}))
    catalog.append(entry("mufap/funds/amc/{slug}.json", "Funds from one AMC",
                         "Every fund managed by a single company. `{slug}` comes from "
                         f"`mufap/funds/amcs.json` — {len(amcs)} of them.",
                         records=None, schema="fund"))

    ratings: dict[str, int] = {}
    trustees: dict[str, int] = {}
    for row in shaped:
        if row.get("rating"):
            ratings[row["rating"]] = ratings.get(row["rating"], 0) + 1
        if row.get("trustee"):
            trustees[row["trustee"]] = trustees.get(row["trustee"], 0) + 1
    writer.write("mufap/funds/ratings.json",
                 envelope([{"rating": k, "count": v} for k, v in sorted(ratings.items())], funds))
    writer.write("mufap/funds/trustees.json",
                 envelope([{"trustee": k, "count": v} for k, v in sorted(trustees.items())], funds))
    catalog.append(entry("mufap/funds/ratings.json", "Stability ratings",
                         "Every published rating with the number of funds carrying it.",
                         records=len(ratings), schema="rating"))
    catalog.append(entry("mufap/funds/trustees.json", "Trustees",
                         "Every trustee institution with the number of funds it holds.",
                         records=len(trustees), schema="trustee"))

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
        writer.write(f"mufap/funds/bottom/{period}.json",
                     envelope(pool[::-1][:TOP_LIMIT], funds, total=len(pool),
                              limit=TOP_LIMIT, extra={"period": period}))
    catalog.append(entry("mufap/funds/bottom/{period}.json", "Worst performers",
                         "The weakest funds over one return period, same periods as "
                         "above.",
                         records=TOP_LIMIT, schema="fund"))
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
    return catalog, shaped


def search_entries(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Project one domain's rows down to what a search needs.

    Called while that domain is loaded, so the projection — a few dozen bytes
    a row — is all that outlives it. Holding both full datasets just to build
    an index would undo the reason they are processed one at a time.
    """
    out: list[dict[str, Any]] = []
    if kind == "stock":
        for row in rows:
            out.append({
                "type": "stock",
                "id": row.get("symbol"),
                "label": row.get("name") or row.get("symbol"),
                "group": row.get("sector"),
                "active": bool(row.get("traded")),
                "endpoint": "psx/stocks/%s.json" % (row.get("symbol") or "").lower(),
            })
    else:
        for row in rows:
            out.append({
                "type": "fund",
                "id": row.get("fund_name"),
                "label": row.get("fund_name"),
                "group": row.get("category"),
                "issuer": row.get("amc"),
                "active": row.get("nav") is not None,
                "endpoint": "mufap/fund/%s.json" % row.get("slug"),
            })
    return out


def write_search(writer: Writer, rows: list[dict[str, Any]],
                 now: datetime) -> dict[str, Any]:
    """A single index covering both domains.

    A static host cannot answer `?q=`, because the query is not known when the
    file is written. The honest equivalent is to publish the smallest thing a
    consumer needs to answer it themselves: identifier, label and grouping for
    every instrument and every fund, and nothing else — no prices, no returns,
    no loads. That is about a third of the two full datasets uncompressed and
    roughly 22 KB on the wire once GitHub gzips it, so a typeahead can hold
    the whole market in memory and match locally.

    `endpoints` maps each row `type` to the file holding its full record, so a
    hit leads somewhere without the consumer hardcoding path conventions.
    """
    payload = {
        "count": len(rows),
        "published_at": now.isoformat(timespec="seconds"),
        "hint": "Match `id`, `label` and `issuer` case-insensitively, then fetch "
                "the hit's own `endpoint` for the full record.",
        "collections": {"stock": "psx/stocks.json", "fund": "mufap/funds.json"},
        "data": rows,
    }
    writer.write("search.json", payload)
    return entry("search.json", "Search index for both domains",
                 "Identifier, label and grouping for every instrument and every fund "
                 "in one file, with no prices or returns. A static host cannot answer "
                 "a `?q=` parameter, so this is what it publishes instead: enough to "
                 "match locally, plus an `endpoints` map to the files holding the full "
                 "records. 224 KB raw, about 22 KB gzipped.",
                 records=len(rows), schema="search")


# ── catalog ───────────────────────────────────────────────────────────────────

def entry(path: str, summary: str, description: str, *,
          records: int | None, schema: str) -> dict[str, Any]:
    return {"path": path, "summary": summary, "description": description,
            "records": records, "schema": schema}


SCHEMAS: dict[str, dict[str, str]] = {
    "stock": {
        "symbol": "Ticker as PSX lists it, e.g. `OGDC`. Lower-cased it is also the path of the instrument's own endpoint.",
        "name": "Registered company name.",
        "sector": "PSX sector classification, resolved from the sector code.",
        "sector_code": "PSX's four-digit sector code.",
        "indices": "Every index the instrument belongs to. Each is addressable as `psx/indices/{name}.json`.",
        "flags": "Market-state badges PSX prints beside the ticker: `NC` non-compliant, `XD` ex-dividend.",
        "current": "Last price the screener published.",
        "change": "Absolute move, derived exactly from `current` and `change_pct`.",
        "change_pct": "Percentage move on the session.",
        "change_1y_pct": "Percentage move over twelve months.",
        "market_cap": "Market capitalisation in PKR.",
        "pe_ratio": "Trailing twelve-month price/earnings.",
        "dividend_yield": "Trailing dividend yield, in percent.",
        "free_float": "Free-float shares.",
        "volume_30d_avg": "Average daily volume over 30 days.",
        "has_quote": "Whether this row carries today's OHLC and session volume. PSX withdrew the bulk quote feed on 2026-09-24, so those are fetched per instrument for a bounded set; see `psx/stocks/quoted.json`.",
        "ldcp": "Last day closing price. Only where `has_quote` is true.",
        "open": "Session opening price. Only where `has_quote` is true.",
        "high": "Session high. Only where `has_quote` is true.",
        "low": "Session low. Only where `has_quote` is true.",
        "volume": "Shares traded in the session. Only where `has_quote` is true.",
        "traded": "Kept for consumers written against the older shape. It now means the instrument is priced in the screener - it is NOT a claim that it traded today. Use `psx/stocks/summary.json` for the exchange's own traded count.",
        "is_etf": "Exchange-traded fund. Carried forward from before PSX withdrew the classification feed.",
        "is_debt": "Debt instrument rather than equity. Carried forward likewise.",
    },
    "segment": {
        "market": "Market segment, e.g. Regular, Deliverable Futures, Bills & Bonds.",
        "state": "`Open` or `Closed`, as the exchange reports it.",
        "trades": "Trades executed in the segment.",
        "volume": "Shares or units traded.",
        "value": "Traded value in PKR.",
    },
    "rating": {"rating": "Stability or performance rating.", "count": "Funds carrying it."},
    "trustee": {"trustee": "Trustee institution.", "count": "Funds it holds."},
    "poll": {
        "published_at": "When this file was written.",
        "datasets": "One entry per dataset, each carrying `state`, `fetched_at`, `data_as_of`, `stale_after_seconds`, `next_refresh_at` and `record_count`.",
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
        "slug": "URL segment addressing this fund's own endpoint, `mufap/fund/{slug}.json`.",
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
        "slug": "URL segment for its own endpoint.",
        "instruments": "Instruments classified into the sector.",
        "gainers": "How many are up on the session.",
        "losers": "How many are down.",
        "unchanged": "How many are flat.",
        "market_cap": "Combined market capitalisation, PKR.",
        "avg_change_pct": "Mean percentage move across the sector.",
    },
    "search": {
        "type": "`stock` or `fund`.",
        "id": "PSX ticker, or the fund name exactly as MUFAP publishes it.",
        "label": "Human-readable name to display.",
        "group": "Sector for a stock, category for a fund.",
        "issuer": "Asset management company. Funds only.",
        "active": "The instrument is priced, or the fund published a NAV.",
        "endpoint": "The path holding this record on its own, e.g. `psx/stocks/ogdc.json`.",
    },
    "category": {"category": "Category name.", "slug": "URL segment for its own endpoint.",
                 "count": "Funds in it."},
    "amc": {"amc": "Company name.", "slug": "URL segment for its own endpoint.",
            "count": "Funds it manages.", "categories": "Categories it operates in."},
    "summary": {
        "listed_instruments": "Instruments in the published universe.",
        "traded_instruments": "How many actually traded, from the exchange's session header.",
        "gainers": "Advancers, from the exchange.",
        "losers": "Decliners, from the exchange.",
        "unchanged": "Closed flat, from the exchange.",
        "total_trades": "Trades executed across the market.",
        "total_volume": "Shares traded across the market.",
        "total_traded_value": "Traded value in PKR.",
        "market_capitalisation": "Combined market cap of the published universe.",
        "avg_change_pct": "Mean percentage move across the universe - derived here, not published by PSX.",
        "instruments_with_full_quote": "How many rows carry today's OHLC and volume.",
        "session_at": "The timestamp PSX stamped the board with.",
        "market_status": "`open` or `closed`, from the segment states.",
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
    session = Dataset("psx.session", "psx", data_dir / "psx.session.json", now)
    # The index board is a view of the same session as the trades, and carries
    # no timestamp of its own; the last trade tick is what it is "as of".
    if not indices.data_as_of:
        indices.data_as_of = stocks.data_as_of
    psx_catalog = build_psx(writer, stocks, indices, session)
    psx_state = {"stocks": stocks.freshness((stocks.meta or {}).get("market_status")),
                 "indices": indices.freshness(),
                 "session": session.freshness()}
    stocks_count, indices_count = stocks.count, indices.count
    search_rows = search_entries(stocks.rows, "stock")
    del stocks, indices, session

    funds = Dataset("mufap.funds", "mufap", data_dir / "mufap.funds.json", now)
    mufap_catalog, shaped_funds = build_mufap(writer, funds)
    mufap_state = {"funds": funds.freshness()}
    funds_count = funds.count
    # The shaped rows, not the raw ones: the slug that addresses each fund's own
    # endpoint is assigned during shaping and exists nowhere else.
    search_rows += search_entries(shaped_funds, "fund")
    del funds, shaped_funds

    search_catalog = write_search(writer, search_rows, now)
    del search_rows

    # The endpoint a production consumer polls. Everything else is measured in
    # hundreds of kilobytes; this is under a kilobyte and answers the only
    # question a poller has: is there anything new, and how old is what I hold.
    POLL_FIELDS = ("state", "fetched_at", "data_as_of", "stale_after_seconds",
                   "next_refresh_at", "record_count")
    poll_datasets: dict[str, Any] = {}
    for domain, states in (("psx", psx_state), ("mufap", mufap_state)):
        for name, state in states.items():
            poll_datasets[f"{domain}.{name}"] = {
                field: state.get(field) for field in POLL_FIELDS
            }
    writer.write("freshness.json", {
        "published_at": now.isoformat(timespec="seconds"),
        "datasets": poll_datasets,
    })
    freshness_catalog = entry(
        "freshness.json", "Fetch times for every dataset",
        "Under a kilobyte: when each dataset was last fetched, what the source "
        "dates it, when the next run is due and how many records it holds — and "
        "nothing else. Poll this rather than re-downloading a dataset to find "
        "out whether it changed.",
        records=None, schema="poll")

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
            freshness_catalog,
            search_catalog,
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
