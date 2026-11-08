"""MUFAP parser and merge tests against real captured markup."""

from __future__ import annotations

from collections import Counter

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
    Sep 09 to Sep 14 — so a single batch-level data date is a summary, and each
    record keeps its own."""
    returns = parsers.parse_returns_tab(mufap_tab1_html)
    prices = parsers.parse_prices_tab(mufap_tab3_html)
    merged = parsers.merge_funds(returns, prices)
    dates = {r["validity_date"] for r in merged if r["validity_date"]}
    assert len(dates) > 1
    # The batch date describes the bulk of the batch, so it is one of the dates
    # present and it is the one the most funds carry.
    counts = Counter(r["validity_date"] for r in merged if r["validity_date"])
    assert parsers.latest_validity(merged) in dates
    assert counts[parsers.latest_validity(merged)] == max(counts.values())


def test_batch_date_is_not_set_by_a_handful_of_forward_dated_funds():
    """MUFAP quotes NAVs with a forward validity, so the newest date in a batch
    is routinely tomorrow and is carried by a small minority of funds.

    Taking the maximum made the envelope claim data as of a day that had not
    happened yet: on 2026-09-27 the live snapshot reported 2026-09-28, a date
    36 of its 553 funds carried while 408 sat on 2026-09-25.
    """
    rows = (
        [{"validity_date": "2026-09-25"}] * 408
        + [{"validity_date": "2026-09-28"}] * 36
        + [{"validity_date": "2026-09-27"}] * 37
    )
    assert parsers.latest_validity(rows) == "2026-09-25"


def test_batch_date_breaks_ties_toward_the_newer_date():
    rows = [{"validity_date": "2026-09-25"}] * 5 + [{"validity_date": "2026-09-26"}] * 5
    assert parsers.latest_validity(rows) == "2026-09-26"


def test_batch_date_is_none_when_no_fund_publishes_one():
    assert parsers.latest_validity([{"validity_date": None}, {}]) is None


# ── merge: the two tabs on different dates ────────────────────────────────────
# MUFAP does not update tab=1 and tab=3 together. These are the shapes seen
# live on 2026-11-06/08, reduced to the fields the merge reads.

def _perf(nav, date, name="HBL Income Fund"):
    return {"sector": "Open-End Funds", "fund_name": name, "category": "Income",
            "return_basis": "annualized", "rating": "A(f)", "benchmark": "x",
            "validity_date": date, "nav": nav,
            "returns": {k: 1.0 for k, _ in parsers.RETURN_PERIODS}}


def _price(nav, date, repurchase, name="HBL Income Fund", offer=None):
    return {"sector": "Open-End Funds", "fund_name": name, "category": "Income",
            "amc": "HBL", "inception_date": None, "offer_price": offer or repurchase,
            "repurchase_price": repurchase, "nav": nav, "validity_date": date,
            "front_end_load": 0.0, "back_end_load": 0.0, "contingent_load": 0.0,
            "market_price": 0.0, "trustee": "CDC"}


def test_merge_takes_the_newer_nav_when_tab3_is_ahead():
    """HBL Income Fund, 2026-11-08: tab=1 117.4659 @ 11-05, tab=3 117.4684 @
    11-06. The merge used to publish tab=1's older NAV and date."""
    [row] = parsers.merge_funds([_perf(117.4659, "2026-11-05")],
                                [_price(117.4684, "2026-11-06", 117.4684)])
    assert row["nav"] == 117.4684
    assert row["validity_date"] == "2026-11-06"
    assert row["repurchase_price"] == 117.4684
    assert row["returns_as_of"] == "2026-11-05"
    assert row["prices_as_of"] == "2026-11-06"
    assert row["returns"]["ytd"] == 1.0  # performance is still carried


def test_merge_never_pairs_a_price_with_another_days_nav():
    """UBL Stock Advantage, 2026-11-06 snapshot: tab=1 248.16 @ 11-04 was
    published beside tab=3's repurchase 250.44 — a newer day's price filed
    under the older date, which a consumer valuing at `repurchase or nav` read
    as the 11-04 price."""
    [row] = parsers.merge_funds([_perf(250.40, "2026-11-09")],
                                [_price(248.16, "2026-11-04", 248.16)])
    assert row["nav"] == 250.40
    assert row["validity_date"] == "2026-11-09"
    # tab=3's prices are for 11-04 and must not appear beside an 11-09 NAV
    assert row["repurchase_price"] is None
    assert row["offer_price"] is None
    assert row["prices_as_of"] == "2026-11-04"


def test_merge_keeps_prices_when_both_tabs_agree():
    [row] = parsers.merge_funds([_perf(53.4522, "2026-11-06")],
                                [_price(53.4522, "2026-11-06", 53.40, offer=54.0)])
    assert row["nav"] == 53.4522
    assert row["repurchase_price"] == 53.40
    assert row["offer_price"] == 54.0


def test_merge_with_no_tab1_counterpart_uses_tab3():
    [row] = parsers.merge_funds([], [_price(10.0, "2026-11-06", 10.0)])
    assert row["nav"] == 10.0
    assert row["validity_date"] == "2026-11-06"
    assert row["repurchase_price"] == 10.0
    assert row["returns_as_of"] is None
