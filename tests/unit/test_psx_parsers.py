"""PSX parser tests, asserting exact values from real captured markup.

Exact-value assertions are the point. A shape-only test ("returns a list of
dicts with a volume key") passes just as happily against the old positional
parser, which reported CNERGY's volume as 4 instead of 104,274,125.
"""

from __future__ import annotations

import pytest

from app.infra.parsing import ColumnMapError
from app.psx import parsers


# ── market watch ──────────────────────────────────────────────────────────────

def test_market_watch_row_count(market_watch_html):
    rows = parsers.parse_market_watch(market_watch_html)
    assert len(rows) == 500


def test_cnergy_every_field_exact(market_watch_html):
    """REGRESSION F-01 — the positional fallback shifted every numeric field.

    It read the sector code 0825 as LDCP, the day's low as `current`, and the
    change percentage as `volume` (4 instead of 104,274,125).
    """
    rows = parsers.parse_market_watch(market_watch_html)
    cnergy = next(r for r in rows if r["symbol"] == "CNERGY")

    assert cnergy["ldcp"] == 12.37
    assert cnergy["open"] == 12.21
    assert cnergy["high"] == 13.05
    assert cnergy["low"] == 11.86
    assert cnergy["current"] == 12.93
    assert cnergy["change"] == 0.56
    assert cnergy["change_pct"] == 4.53
    assert cnergy["volume"] == 104_274_125
    assert cnergy["sector_code"] == "0825"
    assert "KSE100" in cnergy["indices"]


def test_change_pct_column_not_confused_with_change(market_watch_html):
    """CHANGE and CHANGE (%) are adjacent; a loose header match swaps them."""
    rows = parsers.parse_market_watch(market_watch_html)
    prl = next(r for r in rows if r["symbol"] == "PRL")
    assert prl["change"] == 4.70
    assert prl["change_pct"] == 5.81


def test_volume_is_not_truncated(market_watch_html):
    rows = parsers.parse_market_watch(market_watch_html)
    assert max(r["volume"] for r in rows) > 100_000_000


def test_missing_column_raises_rather_than_guessing():
    """REGRESSION F-01 — there must be no positional fallback.

    A changed header must produce an exception (which alerts and keeps the last
    good snapshot) rather than a plausible, wrong batch.
    """
    html = """
    <table><thead><tr>
      <th>SYMBOL</th><th>SECTOR</th><th>SOMETHING</th>
    </tr></thead><tbody><tr>
      <td>ABC</td><td>0825</td><td>1.0</td>
    </tr></tbody></table>
    """
    with pytest.raises(ColumnMapError):
        parsers.parse_market_watch(html)


def test_no_positional_fallback_exists():
    assert not hasattr(parsers, "_parse_market_watch_positional")


# ── symbols ───────────────────────────────────────────────────────────────────

def test_symbols_universe(symbols_payload):
    rows = parsers.parse_symbols(symbols_payload)
    assert len(rows) == 1020
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["CNERGY"]["name"] == "Cnergyico PK  Limited"
    assert by_symbol["CNERGY"]["sector"] == "REFINERY"
    assert by_symbol["AKBLTFC6"]["is_debt"] is True


def test_symbols_rejects_wrong_shape():
    with pytest.raises(ColumnMapError):
        parsers.parse_symbols({"not": "a list"})


# ── merge ─────────────────────────────────────────────────────────────────────

def test_merge_covers_whole_universe(symbols_payload, market_watch_html):
    """market-watch only carries instruments that traded — 500 of 1,020.

    The other 575 must still be visible, marked traded=false, so a consumer can
    tell "did not trade" from "we failed to fetch it".
    """
    symbols = parsers.parse_symbols(symbols_payload)
    quotes = parsers.parse_market_watch(market_watch_html)
    merged = parsers.merge_universe(symbols, quotes)

    traded = [r for r in merged if r["traded"]]
    not_traded = [r for r in merged if not r["traded"]]

    assert len(traded) == 500
    assert len(not_traded) == 575
    assert all(r["current"] is None for r in not_traded)
    assert all(r["volume"] == 0 for r in not_traded)


def test_merge_enriches_with_name_and_sector(symbols_payload, market_watch_html):
    symbols = parsers.parse_symbols(symbols_payload)
    quotes = parsers.parse_market_watch(market_watch_html)
    merged = parsers.merge_universe(symbols, quotes)
    cnergy = next(r for r in merged if r["symbol"] == "CNERGY")
    assert cnergy["name"] == "Cnergyico PK  Limited"
    assert cnergy["sector"] == "REFINERY"


def test_suffixed_symbols_resolve_to_their_base():
    """AICLXD is AICL trading ex-dividend; it is absent from /symbols."""
    assert parsers.base_symbol("AICLXD") == "AICL"
    assert parsers.base_symbol("AMTEXNC") == "AMTEX"
    assert parsers.base_symbol("HBL") == "HBL"


# ── indices ───────────────────────────────────────────────────────────────────

def test_indices_parse(indices_html):
    """REGRESSION F-05 — the old scraper regexed the homepage, where index
    values are rendered client-side, so it always found zero and the endpoint
    returned 404 permanently."""
    rows = parsers.parse_indices(indices_html)
    assert len(rows) == 18

    kse100 = next(r for r in rows if r["index_name"] == "KSE100")
    assert kse100["current"] == 170511.85
    assert kse100["high"] == 170764.79
    assert kse100["low"] == 166141.17
    assert kse100["change"] == 1646.81
    assert kse100["change_pct"] == 0.98


def test_indices_expose_name_alias(indices_html):
    """REGRESSION F-06 — the dashboard reads `name`; the API returned only
    `index_name`, so every index rendered as a dash."""
    rows = parsers.parse_indices(indices_html)
    assert all(r["name"] == r["index_name"] for r in rows)
    assert all(r["value"] == r["current"] for r in rows)


# ── timeseries ────────────────────────────────────────────────────────────────

def test_timeseries_parse(timeseries_payload):
    points = parsers.parse_timeseries(timeseries_payload, "int")
    assert len(points) == 1190
    assert points[0]["timestamp"] < points[-1]["timestamp"]
    assert all("price" in p for p in points)


def test_timeseries_rejects_bad_envelope():
    with pytest.raises(ColumnMapError):
        parsers.parse_timeseries({"status": 0, "data": []}, "int")
