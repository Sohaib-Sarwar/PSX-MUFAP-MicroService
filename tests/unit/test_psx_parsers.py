"""PSX parser tests, asserting exact values from real captured markup.

Exact-value assertions are the point. A shape-only test ("returns a list of
dicts with a price key") passes just as happily against a parser that has
shifted every column by one, which is the failure mode that produces plausible,
wrong financial data rather than an error.

PSX withdrew /symbols, /market-watch and /timeseries on 2026-09-24 — they answer
403 to an XHR client and 404 to anything else. The fixtures here were captured
on 2026-09-25 from the pages that replaced them.
"""

from __future__ import annotations

import pytest

from app.infra.parsing import ColumnMapError
from app.psx import parsers


# ── /screener ─────────────────────────────────────────────────────────────────

def test_screener_row_count(screener_html):
    rows = parsers.parse_screener(screener_html)
    assert len(rows) == 60          # the fixture is trimmed; live is ~747


def test_ogdc_every_field_exact(screener_html):
    """One instrument, every column, exact.

    If the column map ever slips, this fails on the first field rather than
    silently reporting a P/E as a dividend yield.
    """
    rows = parsers.parse_screener(screener_html)
    ogdc = next(r for r in rows if r["symbol"] == "OGDC")

    assert ogdc["name"] == "Oil & Gas Development Company Limited"
    assert ogdc["sector_code"] == "0820"
    assert ogdc["sector"] == "OIL & GAS EXPLORATION COMPANIES"
    assert ogdc["current"] == 316.23
    assert ogdc["change_pct"] == -1.03
    assert ogdc["change_1y_pct"] == 11.80
    assert ogdc["market_cap"] == 1_400_000_000_000.0
    assert ogdc["pe_ratio"] == 5.61
    assert ogdc["dividend_yield"] == 3.81
    assert ogdc["free_float"] == 645_100_000.0
    assert ogdc["volume_30d_avg"] == 3_654_711.0
    assert "KSE100" in ogdc["indices"] and "KSE30" in ogdc["indices"]


def test_magnitude_suffixes_are_scaled_not_truncated(screener_html):
    """`429.4M` is 429,400,000.

    parse_number() alone reads it as 429.4 and under-reports a market
    capitalisation by six orders of magnitude — the kind of wrong that looks
    like a plausible number.
    """
    rows = parsers.parse_screener(screener_html)
    row = next(r for r in rows if r["symbol"] == "786")
    assert row["market_cap"] == 429_400_000.0
    assert row["free_float"] == 7_000_000.0


def test_change_is_derived_and_reconciles_with_the_quote_page(screener_html,
                                                              company_html):
    """The screener prints a percentage but no rupee move, so `change` is
    derived. Deriving it must agree with the LDCP the company page publishes."""
    rows = parsers.parse_screener(screener_html)
    ogdc = next(r for r in rows if r["symbol"] == "OGDC")
    quote = parsers.parse_company(company_html, "OGDC")

    previous_close = ogdc["current"] - ogdc["change"]
    assert abs(previous_close - quote["ldcp"]) < 0.02


def test_name_and_flags_come_from_the_symbol_cell(screener_html):
    """The company name lives in the link's data-title and the market-state
    badge is a sibling node. Reading the cell as text glues them into "AAL NC"
    and loses the name entirely."""
    rows = parsers.parse_screener(screener_html)
    aal = next(r for r in rows if r["symbol"] == "AAL")

    assert aal["name"] == "Agro Allianz Limited"
    assert aal["flags"] == ["NC"]
    assert " " not in aal["symbol"]


def test_every_row_is_named(screener_html):
    rows = parsers.parse_screener(screener_html)
    assert all(r["name"] for r in rows)


def test_screener_without_a_symbol_column_raises(screener_html):
    html = screener_html.replace("SYMBOL", "TICKER")
    with pytest.raises(ColumnMapError):
        parsers.parse_screener(html)


def test_one_year_change_cannot_be_claimed_by_change_pct(screener_html):
    """`CHANGE (%)` and `1-YEAR CH. (%)` share every word that matters. The
    longer header must be claimed first or the two swap."""
    rows = parsers.parse_screener(screener_html)
    ogdc = next(r for r in rows if r["symbol"] == "OGDC")
    assert ogdc["change_pct"] == -1.03
    assert ogdc["change_1y_pct"] == 11.80


# ── /trading-panel ────────────────────────────────────────────────────────────

def test_trading_panel_session_header(trading_panel_html):
    panel = parsers.parse_trading_panel(trading_panel_html)

    assert panel["total_trades"] == 484_489
    assert panel["advancing"] == 100
    assert panel["declining"] == 358
    assert panel["unchanged"] == 36
    assert panel["traded_instruments"] == 494
    assert panel["total_volume"] == 1_286_448_248
    assert panel["total_traded_value"] == 49_156_551_410.0


def test_session_timestamp_is_pkt(trading_panel_html):
    panel = parsers.parse_trading_panel(trading_panel_html)
    assert panel["session_at"] == "2026-09-24T18:27:00+05:00"


def test_market_status_comes_from_segment_states(trading_panel_html):
    """Every segment closed means the session is over. This needs no clock
    arithmetic and no trading calendar, so it stays correct on a public holiday
    and through Ramadan hours."""
    panel = parsers.parse_trading_panel(trading_panel_html)
    assert panel["market_status"] == "closed"
    assert len(panel["segments"]) == 14
    assert panel["segments"][0]["market"] == "Regular"
    assert panel["segments"][0]["trades"] == 372_322


def test_trading_panel_without_a_summary_raises(trading_panel_html):
    html = trading_panel_html.replace("Advance", "Gainers")
    with pytest.raises(ColumnMapError):
        parsers.parse_trading_panel(html)


# ── /company/{symbol} ─────────────────────────────────────────────────────────

def test_company_quote_fields(company_html):
    quote = parsers.parse_company(company_html, "OGDC")

    assert quote["symbol"] == "OGDC"
    assert quote["name"] == "Oil & Gas Development Company Limited"
    assert quote["ldcp"] == 319.53
    assert quote["open"] == 319.53
    assert quote["high"] == 320.91
    assert quote["low"] == 315.40
    assert quote["volume"] == 1_961_861


def test_company_low_is_not_above_high(company_html):
    quote = parsers.parse_company(company_html, "OGDC")
    assert quote["low"] <= quote["high"]


# ── merge ─────────────────────────────────────────────────────────────────────

def test_merge_marks_rows_without_a_quote(screener_html, company_html):
    """`has_quote` must distinguish "no OHLC was fetched" from "did not trade".

    Filling the old field names with nulls and leaving it at that reads as the
    latter, which is a claim this service can no longer support for anything
    outside the bounded quote pass.
    """
    universe = parsers.parse_screener(screener_html)
    details = {"OGDC": parsers.parse_company(company_html, "OGDC")}
    rows = parsers.merge_universe(universe, {}, details)

    ogdc = next(r for r in rows if r["symbol"] == "OGDC")
    other = next(r for r in rows if r["symbol"] != "OGDC")

    assert ogdc["has_quote"] is True
    assert ogdc["volume"] == 1_961_861
    assert other["has_quote"] is False
    assert other["volume"] is None


def test_merge_inherits_classification_it_can_no_longer_fetch(screener_html):
    """is_etf / is_debt came from /symbols, which is gone. They are carried
    forward from the previous snapshot rather than silently dropped."""
    universe = parsers.parse_screener(screener_html)
    inherited = {"OGDC": {"symbol": "OGDC", "is_etf": False, "is_debt": True}}
    rows = parsers.merge_universe(universe, inherited, {})

    ogdc = next(r for r in rows if r["symbol"] == "OGDC")
    assert ogdc["is_debt"] is True
    assert ogdc["is_etf"] is False


def test_merge_keeps_every_screener_row(screener_html):
    universe = parsers.parse_screener(screener_html)
    rows = parsers.merge_universe(universe, {}, {})
    assert len(rows) == len(universe)


# ── /indices ──────────────────────────────────────────────────────────────────

def test_indices_parsed(indices_html):
    rows = parsers.parse_indices(indices_html)
    assert len(rows) == 18
    kse100 = next(r for r in rows if r["index_name"] == "KSE100")
    assert kse100["current"] > 0
    assert kse100["high"] >= kse100["low"]
