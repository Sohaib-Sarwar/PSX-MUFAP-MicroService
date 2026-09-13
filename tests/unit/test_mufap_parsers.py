"""MUFAP parser and merge tests against real captured markup."""

from __future__ import annotations

import pytest

from app.infra.parsing import ColumnMapError, parse_number
from app.mufap import parsers


# ── tab=1 : returns ───────────────────────────────────────────────────────────

def test_returns_tab_row_count(mufap_tab1_html):
    rows = parsers.parse_returns_tab(mufap_tab1_html)
    assert len(rows) == 546


def test_returns_tab_fields_exact(mufap_tab1_html):
    rows = parsers.parse_returns_tab(mufap_tab1_html)
    abl = next(r for r in rows
               if r["fund_name"] == "ABL Cash Fund" and r["category"] == "Money Market")
    assert abl["nav"] == 10.4862
    assert abl["rating"] == "AA+(f)"
    assert abl["validity_date"] == "2026-09-14"
    assert abl["return_basis"] == "annualized"
    assert abl["returns"]["ytd"] == 10.63
    assert abl["returns"]["y3"] == 17.37


def test_accounting_negatives_parse_as_negative():
    """MUFAP renders a negative return as "(6.21)"; reading it as +6.21 would
    invert the sign on every losing fund."""
    assert parse_number("(6.21)") == -6.21
    assert parse_number("6.21") == 6.21
    assert parse_number("2,054.8864") == 2054.8864
    assert parse_number("N/A") is None


def test_negative_returns_present(mufap_tab1_html):
    rows = parsers.parse_returns_tab(mufap_tab1_html)
    negatives = [r for r in rows if (r["returns"].get("ytd") or 0) < 0]
    assert negatives, "equity funds with negative YTD must parse as negative"


# ── tab=3 : prices ────────────────────────────────────────────────────────────

def test_prices_tab_fields_exact(mufap_tab3_html):
    rows = parsers.parse_prices_tab(mufap_tab3_html)
    assert len(rows) == 546

    abl = next(r for r in rows
               if r["fund_name"] == "ABL Cash Fund" and r["category"] == "Money Market")
    assert abl["nav"] == 10.4862
    assert abl["offer_price"] == 10.5774
    assert abl["front_end_load"] == 1.0
    assert abl["back_end_load"] == 2.0
    assert abl["contingent_load"] == 1.0
    assert abl["trustee"] == "CDC"
    assert abl["amc"] == "ABL Asset Management Company Limited"


def test_nav_is_never_the_sales_load(mufap_tab3_html):
    """REGRESSION F-02 — the old positional fallback chose "the last number
    greater than zero" as the NAV. Front-end/Back-end/Contingent load columns
    sit after NAV, so any fund carrying a load had its NAV replaced by it:
    ABL Cash Fund's 10.4862 became 1.0.
    """
    rows = parsers.parse_prices_tab(mufap_tab3_html)
    with_loads = [r for r in rows if (r.get("front_end_load") or 0) > 0]
    assert len(with_loads) > 50, "sample must contain funds carrying a load"
    for row in with_loads:
        assert row["nav"] != row["front_end_load"]
        assert row["nav"] > 1.0


def test_fund_name_is_not_the_sector(mufap_tab3_html):
    """REGRESSION F-02 — the fallback read cell 0 (Sector) as the fund name,
    collapsing 546 funds to 5 distinct names."""
    rows = parsers.parse_prices_tab(mufap_tab3_html)
    names = {r["fund_name"] for r in rows}
    assert len(names) > 400
    assert "Open-End Funds" not in names


def test_validity_date_is_not_the_inception_date(mufap_tab3_html):
    """REGRESSION F-02 — the fallback captured the first parseable date, which
    is Inception, producing validity dates as old as 1962."""
    rows = parsers.parse_prices_tab(mufap_tab3_html)
    dates = {r["validity_date"] for r in rows if r["validity_date"]}
    assert all(d >= "2026-01-01" for d in dates), f"stale validity dates: {sorted(dates)[:3]}"


def test_missing_column_raises():
    html = """
    <table><thead><tr><th>Sector</th><th>Fund</th><th>Something</th></tr></thead>
    <tbody><tr><td>Open-End</td><td>X Fund</td><td>1.0</td></tr></tbody></table>
    """
    with pytest.raises(ColumnMapError):
        parsers.parse_prices_tab(html)


def test_no_positional_fallback_exists():
    assert not hasattr(parsers, "_parse_nav_table_positional")


# ── category normalisation and the join ───────────────────────────────────────

def test_category_normalisation():
    assert parsers.normalise_category("VPS-Debt (Annualized Return )") == ("VPS-Debt", "annualized")
    assert parsers.normalise_category("Equity (Absolute Return )") == ("Equity", "absolute")
    assert parsers.normalise_category("Money Market") == ("Money Market", None)


def test_merge_is_one_to_one(mufap_tab1_html, mufap_tab3_html):
    """The join key must be (sector, fund, category), not fund name alone:
    28 VPS funds share a name across two or three sub-allocations."""
    returns = parsers.parse_returns_tab(mufap_tab1_html)
    prices = parsers.parse_prices_tab(mufap_tab3_html)
    merged = parsers.merge_funds(returns, prices)

    assert len(merged) == 546
    matched = [r for r in merged if r["returns"].get("ytd") is not None or r["rating"]]
    assert len(matched) > 500, "the two tabs must actually join, not sit side by side"


def test_merge_resolves_vps_name_collisions(mufap_tab1_html, mufap_tab3_html):
    returns = parsers.parse_returns_tab(mufap_tab1_html)
    prices = parsers.parse_prices_tab(mufap_tab3_html)
    merged = parsers.merge_funds(returns, prices)

    abl_pension = [r for r in merged if r["fund_name"] == "ABL Pension Fund"]
    assert len(abl_pension) == 3
    navs = {r["nav"] for r in abl_pension}
    assert len(navs) == 3, "each sub-fund must keep its own NAV"
    categories = {r["category"] for r in abl_pension}
    assert categories == {"VPS-Debt", "VPS-Equity", "VPS-Money Market"}


def test_merged_record_carries_both_tabs(mufap_tab1_html, mufap_tab3_html):
    returns = parsers.parse_returns_tab(mufap_tab1_html)
    prices = parsers.parse_prices_tab(mufap_tab3_html)
    merged = parsers.merge_funds(returns, prices)

    abl = next(r for r in merged
               if r["fund_name"] == "ABL Cash Fund" and r["category"] == "Money Market")
    # from tab=3
    assert abl["offer_price"] == 10.5774
    assert abl["trustee"] == "CDC"
    # from tab=1
    assert abl["rating"] == "AA+(f)"
    assert abl["returns"]["ytd"] == 10.63


def test_validity_dates_vary_per_fund(mufap_tab1_html, mufap_tab3_html):
    """Funds do not all publish on the same day — the captured sample spans
    Sep 09 to Sep 14 — so a single batch-level data date would be wrong."""
    returns = parsers.parse_returns_tab(mufap_tab1_html)
    prices = parsers.parse_prices_tab(mufap_tab3_html)
    merged = parsers.merge_funds(returns, prices)
    dates = {r["validity_date"] for r in merged if r["validity_date"]}
    assert len(dates) > 1
    assert parsers.latest_validity(merged) == max(dates)
