"""Tests for analysis/statement_utils — the foundation everyone else builds on."""
from __future__ import annotations

import pandas as pd
import pytest

from warren_bot.analysis.statement_utils import (
    avg,
    cagr,
    latest,
    row,
    safe_div,
    series_values,
)


@pytest.fixture()
def sample_df():
    """yfinance-shape DataFrame: rows = line items (CamelCase), cols = dates (newest first)."""
    return pd.DataFrame(
        {
            pd.Timestamp("2024-12-31"): [100.0, 50.0, 10.0],
            pd.Timestamp("2023-12-31"): [90.0, 45.0, 8.0],
            pd.Timestamp("2022-12-31"): [80.0, 40.0, 6.0],
            pd.Timestamp("2021-12-31"): [70.0, 35.0, 4.0],
        },
        index=["TotalRevenue", "GrossProfit", "NetIncome"],
    )


class TestRow:
    def test_exact_match(self, sample_df):
        s = row(sample_df, ["TotalRevenue"])
        assert s is not None
        assert s.iloc[0] == 100.0

    def test_case_insensitive(self, sample_df):
        # The matcher normalizes case AND ignores spaces/punctuation
        assert row(sample_df, ["totalrevenue"]) is not None
        assert row(sample_df, ["Total Revenue"]) is not None
        assert row(sample_df, ["TOTAL REVENUE"]) is not None

    def test_first_candidate_wins(self, sample_df):
        # If both candidates match, the first one wins (deterministic)
        s = row(sample_df, ["TotalRevenue", "GrossProfit"])
        assert s.iloc[0] == 100.0  # came from TotalRevenue, not GrossProfit

    def test_missing(self, sample_df):
        assert row(sample_df, ["NonexistentRow"]) is None

    def test_none_df(self):
        assert row(None, ["X"]) is None

    def test_empty_df(self):
        assert row(pd.DataFrame(), ["X"]) is None


class TestLatest:
    def test_returns_newest_value(self, sample_df):
        s = sample_df.loc["NetIncome"]
        # NetIncome values are [10, 8, 6, 4] with newest at index 2024-12-31
        assert latest(s) == 10.0

    def test_handles_nan(self):
        s = pd.Series([float("nan"), 5.0, 3.0],
                      index=pd.to_datetime(["2024-12-31", "2023-12-31", "2022-12-31"]))
        # Latest non-NaN value
        assert latest(s) == 5.0

    def test_none_series(self):
        assert latest(None) is None

    def test_empty_series(self):
        assert latest(pd.Series([], dtype=float)) is None


class TestSeriesValues:
    def test_returns_oldest_first(self, sample_df):
        # NetIncome reversed = [4, 6, 8, 10]
        vals = series_values(sample_df.loc["NetIncome"])
        assert vals == [4.0, 6.0, 8.0, 10.0]

    def test_drops_nan(self):
        s = pd.Series([1.0, float("nan"), 3.0],
                      index=pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31"]))
        assert series_values(s) == [1.0, 3.0]


class TestCagr:
    def test_basic_growth(self):
        # 100 → 110 → 121 over 2 years = 10% CAGR
        assert cagr([100.0, 110.0, 121.0]) == pytest.approx(0.10, rel=1e-3)

    def test_too_few_values(self):
        assert cagr([100.0]) is None
        assert cagr([]) is None

    def test_sign_change_returns_none(self):
        # Negative-to-positive (or vice versa) makes CAGR undefined
        assert cagr([-100.0, 100.0]) is None
        assert cagr([100.0, -50.0]) is None

    def test_zero_start_returns_none(self):
        assert cagr([0.0, 100.0]) is None


class TestSafeDiv:
    @pytest.mark.parametrize("a,b,expected", [
        (10.0, 2.0, 5.0),
        (None, 2.0, None),
        (10.0, None, None),
        (10.0, 0.0, None),  # zero denominator → None, not ZeroDivisionError
        (0.0, 5.0, 0.0),
    ])
    def test_safe_div(self, a, b, expected):
        assert safe_div(a, b) == expected


class TestAvg:
    def test_skips_none_and_nan(self):
        assert avg([1.0, None, 3.0, float("nan")]) == 2.0

    def test_empty_returns_none(self):
        assert avg([]) is None

    def test_all_none_returns_none(self):
        assert avg([None, None]) is None
