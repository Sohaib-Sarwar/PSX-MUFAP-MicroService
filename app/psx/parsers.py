"""PSX parsers.

Every parser here is header-driven and strict. There is deliberately no
positional fallback: the previous implementation had one, and when it fired it
shifted every numeric field by one column — reporting a stock's volume as 4
instead of 104,274,125 and its price as the day's low. A positional guess
produces plausible, wrong financial data; raising ColumnMapError produces an
alert and keeps the last good snapshot serving.

PSX withdrew /symbols, /market-watch and /timeseries on 2026-09-24: they answer
403 with an empty body to an XHR client and 404 to anything else, from a full
browser TLS profile with a warmed session. What follows parses the pages that
are still served.

Verified against live markup on 2026-09-25:
  /screener       SYMBOL | SECTOR | LISTED IN | MARKET CAP. | PRICE |
                  CHANGE (%) | 1-YEAR CH. (%) | PE RATIO (TTM) |
                  DIVIDEND YIELD (%) | FREE FLOAT | 30D VOLUME AVG. (747 rows)
  /trading-panel  Date | Time | Total Trades | Advance | Decline | Unchanged |
                  Total | Exchange Volume | Exchange Value, then one
                  Market | State | Trades | Volume | Value row per segment (14)
  /indices        Index | High | Low | Current | Change | % Change     (18 rows)
  /company/SYM    definition list carrying LDCP, Open, High, Low, Volume
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ..infra.config import PKT
from ..infra.parsing import (
    ColumnMapError,
    build_column_map,
    header_cells,
    parse_int,
    parse_number,
    soup_of,
    table_rows,
)

logger = logging.getLogger(__name__)

# Market-state suffixes PSX appends to a ticker on the market-watch board.
# AICLXD is AICL trading ex-dividend; AMTEXNC is AMTEX flagged non-compliant.
# These symbols are absent from /symbols, so enrichment falls back to the base.
_SUFFIXES = ("XD", "XB", "XR", "NC", "NV", "R1", "R2", "R3", "PRS", "FUT")

_MAGNITUDES = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

_SCREENER_SPEC: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol",),
    "sector_code": ("sector",),
    "listed_in": ("listed in", "listed"),
    "market_cap": ("market cap",),
    "current": ("price",),
    # The long headers are claimed before the short ones so that "change (%)"
    # cannot swallow "1-year ch. (%)", which shares every word that matters.
    "change_1y_pct": ("1-year ch", "1 year ch", "1-year change"),
    "change_pct": ("change (%)", "change %", "% change"),
    "pe_ratio": ("pe ratio", "p/e"),
    "dividend_yield": ("dividend yield",),
    "free_float": ("free float",),
    "volume_30d_avg": ("30d volume", "volume avg"),
}
_SCREENER_REQUIRED = ("symbol", "current", "change_pct")

_INDEX_SPEC: dict[str, tuple[str, ...]] = {
    "index_name": ("index",),
    "high": ("high",),
    "low": ("low",),
    "current": ("current", "value"),
    "change_pct": ("% change", "change (%)", "change %"),
    "change": ("change",),
}
_INDEX_REQUIRED = ("index_name", "current", "change", "change_pct")


def base_symbol(symbol: str) -> str:
    """Strip a market-state suffix to recover the listed ticker."""
    for suffix in _SUFFIXES:
        if len(symbol) > len(suffix) + 1 and symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _pick_data_table(soup, required_header: str):
    """Return the first table whose header row contains `required_header`."""
    for table in soup.find_all("table"):
        headers = header_cells(table)
        if headers and any(required_header in h for h in headers):
            return table, headers
    return None, []


# ── /screener ─────────────────────────────────────────────────────────────────

def _scale(text: str | None) -> float | None:
    """Parse PSX's abbreviated magnitudes: 429.4M, 1.4T, 7.0K.

    parse_number() would read "429.4M" as 429.4 and silently under-report a
    market capitalisation by six orders of magnitude, so the suffix is resolved
    here before the number is taken.
    """
    raw = (text or "").strip().replace(",", "").replace("%", "")
    if not raw or raw.lower() in {"-", "--", "n/a", "na", "nil", "none"}:
        return None
    multiplier = _MAGNITUDES.get(raw[-1].upper())
    if multiplier is not None:
        raw = raw[:-1]
    try:
        value = float(raw)
    except ValueError:
        return None
    return value * multiplier if multiplier is not None else value


def parse_sector_map(soup) -> dict[str, str]:
    """Sector code to sector name, read from the screener's own filter.

    The board only ever carried the numeric code. The page that renders it also
    renders the dropdown a human uses to filter by sector, and that dropdown is
    the mapping — so the names come from PSX rather than from a table in this
    repository that would rot the first time a sector is renamed.
    """
    mapping: dict[str, str] = {}
    for select in soup.find_all("select"):
        if (select.get("name") or "").strip().lower() != "sector":
            continue
        for option in select.find_all("option"):
            code = (option.get("value") or "").strip()
            name = option.get_text(strip=True)
            if code and name and code.lower() not in {"symbols", "website"}:
                mapping[code] = name
    return mapping


def parse_screener(html: str) -> list[dict[str, Any]]:
    """Parse the listed universe.

    This is what PSX left standing when it withdrew /symbols and /market-watch.
    It carries no OHLC and no session volume, but it does carry market
    capitalisation, P/E, dividend yield, free float and a 30-day average volume
    that the old board never had.
    """
    soup = soup_of(html)
    table, headers = _pick_data_table(soup, "symbol")
    if table is None:
        raise ColumnMapError("no table with a SYMBOL column found on /screener")

    col = build_column_map(headers, _SCREENER_SPEC, _SCREENER_REQUIRED)
    logger.info("psx_column_map", extra={"source": "screener", "map": col})

    sectors = parse_sector_map(soup)
    if not sectors:
        logger.warning("psx_sector_map_empty",
                       extra={"detail": "sector codes will be reported unresolved"})

    # The symbol cell is markup, not text: it carries the clean ticker in
    # data-order, the company name in the link's data-title, and any market-state
    # flag as a sibling tag. Reading the cell as text glues them into "AAL NC"
    # and throws the name away — which is why the name used to have to come from
    # a separate feed that no longer exists.
    symbol_cells = [
        row.find_all(["td", "th"])[col["symbol"]]
        for row in table.find_all("tr")
        if len(row.find_all(["td", "th"])) > col["symbol"] and row.find("td")
    ]

    records: list[dict[str, Any]] = []
    for cells, node in zip(table_rows(table), symbol_cells):
        def cell(field: str) -> str | None:
            index = col.get(field)
            return cells[index] if index is not None and index < len(cells) else None

        link = node.find("a")
        symbol = (node.get("data-order")
                  or (link.get_text(strip=True) if link else "")
                  or (cell("symbol") or "").split()[0]).strip().upper()
        if not symbol or not re.match(r"^[A-Z0-9]", symbol):
            continue

        name = (link.get("data-title").strip() if link and link.get("data-title") else None)
        flags = [t.get_text(strip=True).upper()
                 for t in node.find_all(class_="tag") if t.get_text(strip=True)]

        price = _scale(cell("current"))
        if price is None:
            continue

        code = (cell("sector_code") or "").strip() or None
        listed_in = cell("listed_in") or ""
        change_pct = _scale(cell("change_pct"))
        # The screener prints a percentage but not the rupee move; it is
        # recoverable exactly, because price is the post-move figure.
        change = None
        if change_pct is not None and change_pct != -100.0:
            previous = price / (1 + change_pct / 100.0)
            change = price - previous

        records.append({
            "symbol": symbol,
            "name": name,
            # NC is non-compliant, XD ex-dividend. PSX renders these as badges
            # beside the ticker and nowhere else.
            "flags": flags,
            "sector_code": code,
            "sector": sectors.get(code) if code else None,
            "indices": [i for i in (p.strip() for p in listed_in.split(",")) if i],
            "current": price,
            "change": change,
            "change_pct": change_pct,
            "change_1y_pct": _scale(cell("change_1y_pct")),
            "market_cap": _scale(cell("market_cap")),
            "pe_ratio": _scale(cell("pe_ratio")),
            "dividend_yield": _scale(cell("dividend_yield")),
            "free_float": _scale(cell("free_float")),
            "volume_30d_avg": _scale(cell("volume_30d_avg")),
        })

    if not records:
        raise ColumnMapError("/screener parsed to zero instruments")
    logger.info("psx_screener_parsed",
                extra={"rows": len(records), "sectors": len(sectors),
                       "named": sum(1 for r in records if r["name"])})
    return records


# ── /trading-panel ────────────────────────────────────────────────────────────

def parse_trading_panel(html: str) -> dict[str, Any]:
    """Parse the session header and the per-market segment states.

    This is the only authoritative timestamp PSX still publishes, and the only
    honest source of advance/decline counts: the screener's percentages cannot
    distinguish "closed unchanged" from "did not trade", so counting them would
    invent breadth rather than report it.
    """
    soup = soup_of(html)
    tables = soup.find_all("table")
    if not tables:
        raise ColumnMapError("no tables found on /trading-panel")

    summary: dict[str, Any] = {}
    segments: list[dict[str, Any]] = []

    for table in tables:
        headers = [h.strip().lower() for h in header_cells(table)]
        rows = table_rows(table)
        if not headers or not rows:
            continue

        if "date" in headers and "advance" in headers:
            record = dict(zip(headers, rows[0]))
            summary = {
                "date": record.get("date"),
                "time": record.get("time"),
                "total_trades": parse_int(record.get("total trades")),
                "advancing": parse_int(record.get("advance")),
                "declining": parse_int(record.get("decline")),
                "unchanged": parse_int(record.get("unchanged")),
                "traded_instruments": parse_int(record.get("total")),
                "total_volume": parse_int(record.get("exchange volume")),
                "total_traded_value": parse_number(record.get("exchange value")),
            }
        elif headers[:2] == ["market", "state"]:
            for cells in rows:
                record = dict(zip(headers, cells))
                if not record.get("market"):
                    continue
                segments.append({
                    "market": record["market"],
                    "state": record.get("state"),
                    "trades": parse_int(record.get("trades")),
                    "volume": parse_int(record.get("volume")),
                    "value": parse_number(record.get("value")),
                })

    if not summary:
        raise ColumnMapError("/trading-panel had no session summary row")

    summary["segments"] = segments
    summary["session_at"] = _session_timestamp(summary.get("date"), summary.get("time"))
    # Every segment closed means the session is over. PSX states this per
    # market, so it needs no clock arithmetic and no trading calendar.
    states = {(s.get("state") or "").strip().lower() for s in segments if s.get("state")}
    summary["market_status"] = (
        "open" if "open" in states else "closed" if states else "unknown"
    )

    logger.info("psx_trading_panel_parsed",
                extra={"segments": len(segments), "status": summary["market_status"],
                       "session_at": summary["session_at"]})
    return summary


def _session_timestamp(date_text: str | None, time_text: str | None) -> str | None:
    """Combine PSX's "Sep 24, 2026" and "6:27 PM" into one ISO instant in PKT."""
    if not date_text:
        return None
    stamp = f"{date_text.strip()} {(time_text or '').strip()}".strip()
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y %H:%M", "%b %d, %Y"):
        try:
            return datetime.strptime(stamp, fmt).replace(tzinfo=PKT).isoformat()
        except ValueError:
            continue
    logger.warning("psx_session_timestamp_unparsed", extra={"raw": stamp})
    return None


# ── /company/{symbol} ─────────────────────────────────────────────────────────

_QUOTE_LABELS = {
    "ldcp": "ldcp",
    "open": "open",
    "high": "high",
    "low": "low",
    "volume": "volume",
}


def parse_company(html: str, symbol: str) -> dict[str, Any]:
    """Parse one instrument's quote page.

    The last remaining source of today's OHLC and session volume. The page is a
    definition list rather than a table, so each figure is taken by its own
    label instead of by position — a layout change drops a field rather than
    shifting every field by one.
    """
    soup = soup_of(html)
    detail: dict[str, Any] = {"symbol": symbol.upper()}

    title = soup.title.get_text(strip=True) if soup.title else ""
    match = re.search(r"quote for (.+?) - Pakistan Stock Exchange", title, re.I)
    if match:
        detail["name"] = match.group(1).strip()

    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    for field, label in _QUOTE_LABELS.items():
        found = re.search(rf"\b{label}\b[^0-9\-]{{0,6}}([\d,]+\.?\d*)", text, re.I)
        if found:
            value = parse_number(found.group(1))
            detail[field] = int(value) if field == "volume" and value is not None else value

    return detail


def merge_universe(
    screener: list[dict[str, Any]],
    inherited: dict[str, dict[str, Any]],
    details: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine the screener universe with today's quotes and prior classification.

    The screener carries the name, sector, price and fundamentals. What it does
    not carry is the ETF/debt classification, which came from /symbols and now
    comes from nowhere — so it is inherited from the previous snapshot rather
    than guessed or dropped.

    `has_quote` says plainly whether a row's OHLC and volume are this session's
    or simply absent. Leaving the old field names filled with nulls instead
    would read as "did not trade", which is a different and wrong claim.
    """
    merged: list[dict[str, Any]] = []

    for row in screener:
        symbol = row["symbol"]
        known = inherited.get(symbol) or inherited.get(base_symbol(symbol)) or {}
        quote = details.get(symbol) or {}

        record = dict(row)
        record["name"] = row.get("name") or known.get("name")
        record["sector"] = row.get("sector") or known.get("sector")
        record["is_etf"] = bool(known.get("is_etf", False))
        record["is_debt"] = bool(known.get("is_debt", False))

        for field in ("ldcp", "open", "high", "low", "volume"):
            record[field] = quote.get(field)
        record["has_quote"] = quote.get("volume") is not None
        # Preserved for consumers written against the old shape. It now means
        # "quoted in the screener", which is the only claim the source supports.
        record["traded"] = record.get("current") is not None
        merged.append(record)

    logger.info("psx_universe_merged",
                extra={"total": len(merged),
                       "named": sum(1 for r in merged if r["name"]),
                       "quoted": sum(1 for r in merged if r["has_quote"])})
    return merged


# ── /indices ──────────────────────────────────────────────────────────────────

def parse_indices(html: str) -> list[dict[str, Any]]:
    """Parse the index board.

    The previous implementation regexed the PSX homepage, where index values are
    rendered client-side — so it always found zero and /api/psx/indices returned
    404 permanently. This page renders them server-side and additionally carries
    High and Low.
    """
    soup = soup_of(html)
    table, headers = _pick_data_table(soup, "index")
    if table is None:
        raise ColumnMapError("no table with an INDEX column found on /indices")

    col = build_column_map(headers, _INDEX_SPEC, _INDEX_REQUIRED)
    logger.info("psx_column_map", extra={"source": "indices", "map": col})

    records: list[dict[str, Any]] = []
    for cells in table_rows(table):
        def cell(field: str) -> str | None:
            index = col.get(field)
            return cells[index] if index is not None and index < len(cells) else None

        name = (cell("index_name") or "").strip()
        current = parse_number(cell("current"))
        if not name or current is None:
            continue
        records.append({
            "index_name": name,
            "name": name,          # alias: the dashboard reads `name`
            "value": current,      # alias kept for the existing API contract
            "current": current,
            "high": parse_number(cell("high")),
            "low": parse_number(cell("low")),
            "change": parse_number(cell("change")),
            "change_pct": parse_number(cell("change_pct")),
        })

    logger.info("psx_indices_parsed", extra={"rows": len(records)})
    return records
