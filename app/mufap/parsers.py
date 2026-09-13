"""MUFAP parsers.

MUFAP splits fund data across two tabs of the same page, and neither is
complete on its own:

  tab=1  Sector | Category | Fund Name | Rating | Benchmark | Validity Date |
         NAV | YTD | MTD | 1 Day | 15/30/90/180/270/365 Days | 2/3 Years
  tab=3  Sector | AMC | Fund | Category | Inception Date | Offer | Repurchase |
         NAV | Validity Date | Front-end | Back-end | Contingent | Market | Trustee

So tab=1 has performance and rating but no prices or loads; tab=3 has prices,
loads and the trustee but no performance. This module scrapes both and joins
them on (sector, fund name, normalised category) — verified to be a perfect
1:1 match across all 549 rows on live data.

The join cannot use fund name alone: 28 VPS pension funds appear two or three
times under one name, once per sub-allocation (Money Market / Debt / Equity),
and only Category distinguishes them. tab=1 suffixes its Category with the
return basis, e.g. "VPS-Debt (Annualized Return )", which is stripped for the
join and surfaced separately as `return_basis`.

Like the PSX parsers, there is no positional fallback. The previous one took
the fund name from the Sector column (5 distinct names for 546 funds) and
selected the sales load as the NAV whenever a fund carried one — understating
ABL Cash Fund's NAV as 1.0 against a true 10.4862.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..infra.parsing import (
    ColumnMapError,
    build_column_map,
    header_cells,
    parse_date,
    parse_number,
    soup_of,
    table_rows,
)

logger = logging.getLogger(__name__)

# Return periods on tab=1, in the order MUFAP presents them.
RETURN_PERIODS: tuple[tuple[str, str], ...] = (
    ("ytd", "ytd"),
    ("mtd", "mtd"),
    ("d1", "1 day"),
    ("d15", "15 days"),
    ("d30", "30 days"),
    ("d90", "90 days"),
    ("d180", "180 days"),
    ("d270", "270 days"),
    ("d365", "365 days"),
    ("y2", "2 years"),
    ("y3", "3 years"),
)

_RETURNS_SPEC: dict[str, tuple[str, ...]] = {
    "sector": ("sector",),
    "category": ("category",),
    "fund_name": ("fund name", "fund"),
    "rating": ("rating",),
    "benchmark": ("benchmark",),
    "validity_date": ("validity date",),
    "nav": ("nav",),
    **{key: (header,) for key, header in RETURN_PERIODS},
}
_RETURNS_REQUIRED = ("sector", "category", "fund_name", "validity_date", "nav")

_PRICES_SPEC: dict[str, tuple[str, ...]] = {
    "sector": ("sector",),
    "amc": ("amc",),
    "fund_name": ("fund", "fund name"),
    "category": ("category",),
    "inception_date": ("inception date", "inception"),
    "offer_price": ("offer",),
    "repurchase_price": ("repurchase", "redemption"),
    "nav": ("nav",),
    "validity_date": ("validity date",),
    "front_end_load": ("front-end", "front end"),
    "back_end_load": ("back-end", "back end"),
    "contingent_load": ("contingent",),
    "market_price": ("market",),
    "trustee": ("trustee",),
}
_PRICES_REQUIRED = ("sector", "fund_name", "category", "nav", "validity_date")

_RETURN_BASIS_RE = re.compile(r"\s*\((Annualized|Absolute)\s+Return\s*\)\s*$", re.I)


def normalise_category(category: str) -> tuple[str, str | None]:
    """Split "VPS-Debt (Annualized Return )" into ("VPS-Debt", "annualized")."""
    match = _RETURN_BASIS_RE.search(category or "")
    if not match:
        return (category or "").strip(), None
    return _RETURN_BASIS_RE.sub("", category).strip(), match.group(1).lower()


def join_key(sector: str, fund_name: str, category: str) -> tuple[str, str, str]:
    base, _ = normalise_category(category)
    return (sector.strip().lower(), fund_name.strip().lower(), base.strip().lower())


def _find_table(soup, *required_headers: str):
    for table in soup.find_all("table"):
        headers = header_cells(table)
        if headers and all(any(req in h for h in headers) for req in required_headers):
            return table, headers
    return None, []


def _row_reader(cells: list[str], col: dict[str, int]):
    def read(field: str) -> str | None:
        index = col.get(field)
        return cells[index] if index is not None and index < len(cells) else None
    return read


# ── tab=1 : performance ───────────────────────────────────────────────────────

def parse_returns_tab(html: str) -> list[dict[str, Any]]:
    """Parse tab=1 — NAV, rating, benchmark and every return period."""
    soup = soup_of(html)
    table, headers = _find_table(soup, "fund", "nav")
    if table is None:
        raise ColumnMapError("no fund/NAV table found on MUFAP tab=1")

    col = build_column_map(headers, _RETURNS_SPEC, _RETURNS_REQUIRED)
    logger.info("mufap_column_map", extra={"source": "tab1", "map": col})

    out: list[dict[str, Any]] = []
    for cells in table_rows(table):
        read = _row_reader(cells, col)
        fund_name = (read("fund_name") or "").strip()
        nav = parse_number(read("nav"))
        if not fund_name or nav is None or nav <= 0:
            continue

        raw_category = (read("category") or "").strip()
        category, basis = normalise_category(raw_category)
        returns = {key: parse_number(read(key)) for key, _ in RETURN_PERIODS}

        out.append({
            "sector": (read("sector") or "").strip(),
            "fund_name": fund_name,
            "category": category or None,
            "return_basis": basis,
            "rating": (read("rating") or "").strip() or None,
            "benchmark": (read("benchmark") or "").strip() or None,
            "validity_date": parse_date(read("validity_date")),
            "nav": nav,
            "returns": returns,
        })

    logger.info("mufap_tab1_parsed", extra={"rows": len(out)})
    return out


# ── tab=3 : pricing ───────────────────────────────────────────────────────────

def parse_prices_tab(html: str) -> list[dict[str, Any]]:
    """Parse tab=3 — AMC, inception, offer/repurchase, sales loads, trustee."""
    soup = soup_of(html)
    table, headers = _find_table(soup, "fund", "nav")
    if table is None:
        raise ColumnMapError("no fund/NAV table found on MUFAP tab=3")

    col = build_column_map(headers, _PRICES_SPEC, _PRICES_REQUIRED)
    logger.info("mufap_column_map", extra={"source": "tab3", "map": col})

    out: list[dict[str, Any]] = []
    for cells in table_rows(table):
        read = _row_reader(cells, col)
        fund_name = (read("fund_name") or "").strip()
        nav = parse_number(read("nav"))
        if not fund_name or nav is None or nav <= 0:
            continue

        category, _ = normalise_category((read("category") or "").strip())
        out.append({
            "sector": (read("sector") or "").strip(),
            "fund_name": fund_name,
            "category": category or None,
            "amc": (read("amc") or "").strip() or None,
            "inception_date": parse_date(read("inception_date")),
            "offer_price": parse_number(read("offer_price")),
            "repurchase_price": parse_number(read("repurchase_price")),
            "nav": nav,
            "validity_date": parse_date(read("validity_date")),
            "front_end_load": parse_number(read("front_end_load")),
            "back_end_load": parse_number(read("back_end_load")),
            "contingent_load": parse_number(read("contingent_load")),
            "market_price": parse_number(read("market_price")),
            "trustee": (read("trustee") or "").strip() or None,
        })

    logger.info("mufap_tab3_parsed", extra={"rows": len(out)})
    return out


# ── merge ─────────────────────────────────────────────────────────────────────

def merge_funds(
    returns_rows: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join the two tabs into one complete fund record.

    tab=3 is the base because it carries the identity fields (AMC, trustee,
    inception). tab=1 contributes performance, rating and benchmark. A fund
    present in only one tab is still returned, with the other side's fields
    left null — a partial record is honest, a dropped one is not.
    """
    by_key = {
        join_key(r["sector"], r["fund_name"], r.get("category") or ""): r
        for r in returns_rows
    }

    merged: list[dict[str, Any]] = []
    matched = 0

    for price in price_rows:
        key = join_key(price["sector"], price["fund_name"], price.get("category") or "")
        perf = by_key.pop(key, None)
        if perf:
            matched += 1

        # tab=1 is refreshed marginally ahead of tab=3 for some funds, so prefer
        # its validity date and NAV when the two disagree.
        validity = (perf or {}).get("validity_date") or price.get("validity_date")
        nav = (perf or {}).get("nav") or price["nav"]

        merged.append({
            "fund_name": price["fund_name"],
            "sector": price["sector"],
            "category": price.get("category"),
            "amc": price.get("amc"),
            "trustee": price.get("trustee"),
            "rating": (perf or {}).get("rating"),
            "benchmark": (perf or {}).get("benchmark"),
            "inception_date": price.get("inception_date"),
            "nav": nav,
            "offer_price": price.get("offer_price"),
            "repurchase_price": price.get("repurchase_price"),
            "market_price": price.get("market_price"),
            "front_end_load": price.get("front_end_load"),
            "back_end_load": price.get("back_end_load"),
            "contingent_load": price.get("contingent_load"),
            "validity_date": validity,
            "return_basis": (perf or {}).get("return_basis"),
            "returns": (perf or {}).get("returns") or {k: None for k, _ in RETURN_PERIODS},
        })

    # Anything left in tab=1 had no tab=3 counterpart; keep it rather than drop it.
    for leftover in by_key.values():
        merged.append({
            "fund_name": leftover["fund_name"],
            "sector": leftover["sector"],
            "category": leftover.get("category"),
            "amc": None, "trustee": None,
            "rating": leftover.get("rating"),
            "benchmark": leftover.get("benchmark"),
            "inception_date": None,
            "nav": leftover["nav"],
            "offer_price": None, "repurchase_price": None, "market_price": None,
            "front_end_load": None, "back_end_load": None, "contingent_load": None,
            "validity_date": leftover.get("validity_date"),
            "return_basis": leftover.get("return_basis"),
            "returns": leftover.get("returns"),
        })

    logger.info(
        "mufap_merged",
        extra={"total": len(merged), "matched": matched,
               "prices_only": len(price_rows) - matched,
               "returns_only": len(by_key)},
    )
    return merged


def latest_validity(rows: list[dict[str, Any]]) -> str | None:
    """The most recent validity date across the batch.

    Funds do not all publish on the same day — the live sample spanned Sep 09
    to Sep 14 — so a single "data date" is the newest one present, and each
    record keeps its own.
    """
    dates = [r.get("validity_date") for r in rows if r.get("validity_date")]
    return max(dates) if dates else None
