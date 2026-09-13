"""PSX parsers.

Every parser here is header-driven and strict. There is deliberately no
positional fallback: the previous implementation had one, and when it fired it
shifted every numeric field by one column — reporting a stock's volume as 4
instead of 104,274,125 and its price as the day's low. A positional guess
produces plausible, wrong financial data; raising ColumnMapError produces an
alert and keeps the last good snapshot serving.

Verified against live markup on 2026-09-13:
  /market-watch  SYMBOL | SECTOR | LISTED IN | LDCP | OPEN | HIGH | LOW |
                 CURRENT | CHANGE | CHANGE (%) | VOLUME          (500 rows)
  /indices       Index | High | Low | Current | Change | % Change  (18 rows)
  /symbols       JSON: symbol, name, sectorName, isETF, isDebt   (1020 items)
"""

from __future__ import annotations

import logging
import re
from typing import Any

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

_MARKET_WATCH_SPEC: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol",),
    "sector_code": ("sector",),
    "listed_in": ("listed in", "listed"),
    "ldcp": ("ldcp",),
    "open": ("open",),
    "high": ("high",),
    "low": ("low",),
    "current": ("current", "close"),
    # change_pct is resolved before change so the exact "change (%)" header is
    # claimed first and plain "change" cannot swallow it.
    "change_pct": ("change (%)", "% change", "change %", "changepercent"),
    "change": ("change",),
    "volume": ("volume",),
}
_MARKET_WATCH_REQUIRED = ("symbol", "ldcp", "open", "high", "low", "current",
                          "change", "change_pct", "volume")

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


# ── /market-watch ─────────────────────────────────────────────────────────────

def parse_market_watch(html: str) -> list[dict[str, Any]]:
    """Parse the full market-watch board.

    Only symbols that traded in the session appear here (500 of 1,020 listed
    instruments on the verified sample), ordered by volume descending.
    """
    soup = soup_of(html)
    table, headers = _pick_data_table(soup, "symbol")
    if table is None:
        raise ColumnMapError("no table with a SYMBOL column found on /market-watch")

    col = build_column_map(headers, _MARKET_WATCH_SPEC, _MARKET_WATCH_REQUIRED)
    logger.info("psx_column_map", extra={"source": "market-watch", "map": col})

    records: list[dict[str, Any]] = []
    for cells in table_rows(table):
        if len(cells) <= col["volume"]:
            continue

        def cell(field: str) -> str | None:
            index = col.get(field)
            return cells[index] if index is not None and index < len(cells) else None

        symbol = (cell("symbol") or "").strip().upper()
        if not symbol or not re.match(r"^[A-Z0-9]", symbol):
            continue

        current = parse_number(cell("current"))
        if current is None:
            # A board row with no current price is a header or spacer row.
            continue

        listed_in = cell("listed_in") or ""
        records.append({
            "symbol": symbol,
            "sector_code": (cell("sector_code") or "").strip() or None,
            "indices": [i for i in (p.strip() for p in listed_in.split(",")) if i],
            "ldcp": parse_number(cell("ldcp")),
            "open": parse_number(cell("open")),
            "high": parse_number(cell("high")),
            "low": parse_number(cell("low")),
            "current": current,
            "change": parse_number(cell("change")),
            "change_pct": parse_number(cell("change_pct")),
            "volume": parse_int(cell("volume")) or 0,
            "traded": True,
        })

    logger.info("psx_market_watch_parsed", extra={"rows": len(records)})
    return records


# ── /symbols ──────────────────────────────────────────────────────────────────

def parse_symbols(payload: Any) -> list[dict[str, Any]]:
    """Parse the full instrument universe.

    This is the only source of company names, sector names and the ETF/debt
    classification. market-watch carries none of them.
    """
    if not isinstance(payload, list):
        raise ColumnMapError(f"/symbols returned {type(payload).__name__}, expected a list")

    out: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "name": (item.get("name") or "").strip() or None,
            "sector": (item.get("sectorName") or "").strip() or None,
            "is_etf": bool(item.get("isETF")),
            "is_debt": bool(item.get("isDebt")),
        })

    if not out:
        raise ColumnMapError("/symbols returned no usable instruments")
    logger.info("psx_symbols_parsed", extra={"rows": len(out)})
    return out


def merge_universe(
    symbols: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine the listed universe with the session's quotes.

    Every listed instrument is returned. Those that traded carry live prices and
    `traded: true`; the rest carry nulls and `traded: false`, so a consumer can
    tell "did not trade" apart from "we failed to fetch it".
    """
    by_symbol = {s["symbol"]: s for s in symbols}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for quote in quotes:
        symbol = quote["symbol"]
        seen.add(symbol)
        meta = by_symbol.get(symbol) or by_symbol.get(base_symbol(symbol)) or {}
        merged.append({
            **quote,
            "name": meta.get("name"),
            "sector": meta.get("sector"),
            "is_etf": meta.get("is_etf", False),
            "is_debt": meta.get("is_debt", False),
        })

    for symbol, meta in by_symbol.items():
        if symbol in seen:
            continue
        merged.append({
            "symbol": symbol,
            "name": meta.get("name"),
            "sector": meta.get("sector"),
            "sector_code": None,
            "indices": [],
            "is_etf": meta.get("is_etf", False),
            "is_debt": meta.get("is_debt", False),
            "ldcp": None, "open": None, "high": None, "low": None,
            "current": None, "change": None, "change_pct": None,
            "volume": 0,
            "traded": False,
        })

    logger.info(
        "psx_universe_merged",
        extra={"total": len(merged), "traded": len(seen),
               "not_traded": len(merged) - len(seen)},
    )
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


# ── /timeseries ───────────────────────────────────────────────────────────────

def parse_timeseries(payload: Any, kind: str) -> list[dict[str, Any]]:
    """Parse an intraday or end-of-day series.

    int: [epoch, price, volume]         eod: [epoch, close, volume, prev_close]
    """
    if not isinstance(payload, dict) or payload.get("status") != 1:
        raise ColumnMapError(f"/timeseries/{kind} returned an unexpected envelope")

    rows = payload.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        point: dict[str, Any] = {
            "timestamp": int(row[0]),
            "price": float(row[1]),
        }
        if len(row) > 2 and row[2] is not None:
            point["volume"] = int(row[2])
        if kind == "eod" and len(row) > 3 and row[3] is not None:
            point["previous_close"] = float(row[3])
        out.append(point)

    out.sort(key=lambda p: p["timestamp"])
    return out
