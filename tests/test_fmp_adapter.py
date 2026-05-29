"""Tests for the Financial Modeling Prep (FMP) adapter.

``get_json`` is patched so no real HTTP happens; fixtures mimic FMP's JSON arrays
(most-recent first, keyed by a fiscal-period-end ``date``). A real Cache backed by
``tmp_path`` exercises the caching path end to end.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from warren_bot.data.cache import Cache
from warren_bot.data.adapters.fmp import FmpAdapter
from warren_bot.data import schema


_INCOME = [
    {"date": "2023-09-30", "revenue": 383285000000, "netIncome": 96995000000,
     "grossProfit": 169148000000, "operatingIncome": 114301000000,
     "epsdiluted": 6.13, "weightedAverageShsOutDil": 15812547000},
    {"date": "2022-09-24", "revenue": 394328000000, "netIncome": 99803000000,
     "grossProfit": 170782000000, "operatingIncome": 119437000000,
     "epsdiluted": 6.11, "weightedAverageShsOutDil": 16325819000},
]

_BALANCE = [
    {"date": "2023-09-30", "totalAssets": 352583000000,
     "totalStockholdersEquity": 62146000000, "longTermDebt": 95281000000,
     "totalCurrentAssets": 143566000000, "totalCurrentLiabilities": 145308000000,
     "cashAndCashEquivalents": 29965000000},
    {"date": "2022-09-24", "totalAssets": 352755000000,
     "totalStockholdersEquity": 50672000000, "longTermDebt": 98959000000,
     "totalCurrentAssets": 135405000000, "totalCurrentLiabilities": 153982000000,
     "cashAndCashEquivalents": 23646000000},
]

_CASHFLOW = [
    {"date": "2023-09-30", "operatingCashFlow": 110543000000,
     "capitalExpenditure": -10959000000, "freeCashFlow": 99584000000,
     "depreciationAndAmortization": 11519000000},
    {"date": "2022-09-24", "operatingCashFlow": 122151000000,
     "capitalExpenditure": -10708000000, "freeCashFlow": 111443000000,
     "depreciationAndAmortization": 11104000000},
]

_PROFILE = [
    {"price": 189.84, "mktCap": 2960000000000, "symbol": "AAPL"},
]


def _fake_get_json(url, *, params=None, headers=None, limiter=None, **kwargs):
    if "income-statement" in url:
        return _INCOME
    if "balance-sheet-statement" in url:
        return _BALANCE
    if "cash-flow-statement" in url:
        return _CASHFLOW
    if "profile" in url:
        return _PROFILE
    return None


@pytest.fixture()
def cache(tmp_path):
    c = Cache(tmp_path / "fmp.sqlite", ttl_seconds=3600)
    yield c
    c.close()


def test_statements_have_canonical_labels_and_timestamp_columns(cache):
    with patch("warren_bot.data.adapters.fmp.get_json", side_effect=_fake_get_json):
        adapter = FmpAdapter(cache, api_key="KEY")
        result = adapter.fetch("AAPL")

    assert result.error is None

    assert result.income is not None
    assert "Total Revenue" in result.income.index
    assert "Net Income" in result.income.index
    assert all(isinstance(c, pd.Timestamp) for c in result.income.columns)

    assert result.balance is not None
    assert "Total Assets" in result.balance.index
    assert all(isinstance(c, pd.Timestamp) for c in result.balance.columns)

    assert result.cashflow is not None
    assert "Operating Cash Flow" in result.cashflow.index
    assert "Capital Expenditure" in result.cashflow.index
    assert all(isinstance(c, pd.Timestamp) for c in result.cashflow.columns)

    # Values land on the right cells.
    ts = pd.Timestamp("2023-09-30")
    assert result.income.loc["Total Revenue", ts] == 383285000000
    assert result.balance.loc["Total Assets", ts] == 352583000000
    assert result.cashflow.loc["Operating Cash Flow", ts] == 110543000000


def test_quote_has_price_and_market_cap(cache):
    with patch("warren_bot.data.adapters.fmp.get_json", side_effect=_fake_get_json):
        result = FmpAdapter(cache, api_key="KEY").fetch("AAPL")

    assert result.quote["price"] == 189.84
    assert result.quote["market_cap"] == 2960000000000
    # shares come from the latest income statement
    assert result.quote["shares_outstanding"] == 15812547000


def test_disabled_adapter_makes_no_http_calls(cache):
    with patch("warren_bot.data.adapters.fmp.get_json", side_effect=_fake_get_json) as gj:
        adapter = FmpAdapter(cache, api_key="")
        assert adapter.enabled is False
        result = adapter.fetch("AAPL")

    assert result.error == "fmp disabled"
    gj.assert_not_called()


def test_negate_fmp_honored():
    """No schema metric currently sets negate_fmp=True, so verify the flag is
    honored generically: capex (negate_fmp=False) must pass through unchanged,
    and any negate_fmp=True metric would be sign-flipped."""
    capex = schema.BY_LABEL["Capital Expenditure"]
    assert capex.negate_fmp is False

    from warren_bot.data.adapters.fmp import _series

    rows = [{"date": "2023-09-30", "capitalExpenditure": -10959000000}]
    s = _series(rows, capex.fmp, negate=capex.negate_fmp)
    # Passes through unchanged (already negative, matching yfinance).
    assert s.iloc[0] == -10959000000

    # Generic negation flips the sign when the flag is set.
    flipped = _series(rows, capex.fmp, negate=True)
    assert flipped.iloc[0] == 10959000000


def test_cache_avoids_second_fetch(cache):
    with patch("warren_bot.data.adapters.fmp.get_json", side_effect=_fake_get_json) as gj:
        adapter = FmpAdapter(cache, api_key="KEY")
        adapter.fetch("AAPL")
        first_calls = gj.call_count
        adapter.fetch("AAPL")
        assert gj.call_count == first_calls  # everything served from cache
