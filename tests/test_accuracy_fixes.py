"""Regression tests for the calculation/data-accuracy fixes.

Each test pins one of the specific correctness issues found in the accuracy
review so they can't silently regress.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from warren_bot.analysis.ratios import compute_ratios
from warren_bot.analysis.scorer import score_ticker
from warren_bot.analysis.statement_utils import (
    aligned,
    dividend_yield_pct,
    frame,
    trend_growth,
)
from warren_bot.analysis.valuation import compute_valuation
from warren_bot.data.fetcher import TickerSnapshot


def _cols(*years: int) -> list[pd.Timestamp]:
    # yfinance orders columns newest-first.
    return [pd.Timestamp(f"{y}-12-31") for y in sorted(years, reverse=True)]


def _df(rows: dict[str, list[float]], years: list[int]) -> pd.DataFrame:
    """Build a yfinance-shaped statement DataFrame: line-item rows, date columns
    (newest-first). Each row list must be given newest-first to match `years`."""
    cols = _cols(*years)
    data = {c: [rows[k][i] for k in rows] for i, c in enumerate(cols)}
    return pd.DataFrame(data, index=list(rows.keys()))


def _snap(**kw) -> TickerSnapshot:
    return TickerSnapshot(ticker=kw.pop("ticker", "TST"), **kw)


# --------------------------------------------------------------------------
# #2 date-alignment: ratios pair the SAME fiscal year even when statements have
# different numbers of periods.
# --------------------------------------------------------------------------
def test_roe_aligns_on_dates_not_position():
    # Income carries 4 years; balance only the latest 3. Positional pairing would
    # marry 2021 net income to 2022 equity. Date alignment must use 2022-2024 only.
    income = _df({"Net Income": [40, 30, 20, 10]}, [2024, 2023, 2022, 2021])
    # equity newest-first for 2024,2023,2022
    balance = _df({"Stockholders Equity": [400, 300, 200]}, [2024, 2023, 2022])
    r = compute_ratios(_snap(income=income, balance=balance))
    # Only the 3 shared years -> ROE = 10% each (40/400, 30/300, 20/200).
    assert r.roe_pct_series == [pytest.approx(10.0)] * 3
    assert r.roe_pct_avg == pytest.approx(10.0)


def test_frame_and_aligned_intersect_dates():
    # a has 3 years, b has 2; alignment keeps only shared dates, oldest-first.
    a = pd.Series([3.0, 2.0, 1.0], index=_cols(2024, 2023, 2022))  # 24=3,23=2,22=1
    b = pd.Series([30.0, 20.0], index=_cols(2024, 2023))           # 24=30,23=20
    f = aligned(frame(a=a, b=b), "a", "b")
    # shared dates {2023, 2024} oldest-first -> a=[2,3], b=[20,30]
    assert list(f["a"]) == [2.0, 3.0]
    assert list(f["b"]) == [20.0, 30.0]


# --------------------------------------------------------------------------
# #4 negative shareholders' equity must NOT produce a spuriously perfect D/E.
# --------------------------------------------------------------------------
def test_negative_equity_debt_to_equity_is_none():
    income = _df({"Net Income": [10, 10]}, [2024, 2023])
    balance = _df({"Stockholders Equity": [-50, -40], "Total Debt": [100, 90]},
                  [2024, 2023])
    r = compute_ratios(_snap(income=income, balance=balance))
    assert r.debt_to_equity is None


# --------------------------------------------------------------------------
# #8 ROIC must not explode / drop out for net-cash companies.
# --------------------------------------------------------------------------
def test_roic_finite_for_net_cash_company():
    income = _df({"Operating Income": [100, 100], "Tax Provision": [21, 21],
                  "Pretax Income": [100, 100]}, [2024, 2023])
    # Cash dwarfs equity+debt: old "equity+debt-cash" basis went negative -> None.
    balance = _df({"Stockholders Equity": [200, 200], "Total Debt": [50, 50],
                   "Cash And Cash Equivalents": [10_000, 10_000]}, [2024, 2023])
    r = compute_ratios(_snap(income=income, balance=balance))
    assert r.roic_pct_avg is not None
    # NOPAT = 100*(1-0.21)=79; invested = 200+50=250; ROIC = 79/250 = 31.6%
    assert r.roic_pct_avg == pytest.approx(31.6, abs=0.1)


# --------------------------------------------------------------------------
# #1 missing metrics ('na') are EXCLUDED from a dimension average rather than
# counted as a hard 0 that deflates the score.
# --------------------------------------------------------------------------
def test_na_cells_excluded_from_dimension(base_settings):
    # Financial Strength: great D/E and current ratio, but NO interest data.
    income = _df({"Net Income": [10, 10], "Total Revenue": [100, 100]}, [2024, 2023])
    balance = _df({
        "Stockholders Equity": [100, 100], "Total Debt": [20, 20],
        "Current Assets": [200, 200], "Current Liabilities": [100, 100],
    }, [2024, 2023])
    ts = score_ticker(_snap(info={"shortName": "T", "sector": "Tech"},
                            income=income, balance=balance,
                            cashflow=_df({"Operating Cash Flow": [1, 1]}, [2024, 2023])),
                      base_settings)
    strength = next(d for d in ts.dimensions if d.name == "Financial Strength")
    icov = next(c for c in strength.cells if c.criterion == "interest_coverage")
    assert icov.status == "na"
    # D/E (0.2 -> 100) and current ratio (2.0 -> 85), interest coverage excluded.
    # If 'na' were counted as 0 the score would be ~61.7; excluded it's ~92.5.
    assert strength.score == pytest.approx((100 + 85) / 2, abs=0.5)


def test_data_coverage_reported(base_settings):
    income = _df({"Net Income": [10, 10], "Total Revenue": [100, 100]}, [2024, 2023])
    ts = score_ticker(_snap(info={"shortName": "T"}, income=income), base_settings)
    assert 0.0 < ts.data_coverage <= 1.0


# --------------------------------------------------------------------------
# #1b The na-exclusion logic must hold at the DIMENSION level too: a dimension
# with no usable cells (e.g. no cash-flow statement -> the whole Valuation block
# is n/a) must NOT contribute weight*0 and deflate the total. The total is
# renormalized over the dimensions that actually carry data.
# --------------------------------------------------------------------------
def test_total_renormalized_over_dimensions_with_data(base_settings):
    years = [2024, 2023, 2022, 2021]
    income = _df({
        "Total Revenue": [1000, 950, 900, 850],
        "Net Income": [250, 230, 210, 190],
        "Gross Profit": [700, 665, 630, 595],
        "Operating Income": [300, 285, 270, 255],
        "Tax Provision": [60, 57, 54, 51],
        "Pretax Income": [300, 285, 270, 255],
    }, years)
    balance = _df({
        "Stockholders Equity": [800, 750, 700, 650],
        "Total Debt": [100, 100, 100, 100],
        "Current Assets": [500, 480, 460, 440],
        "Current Liabilities": [200, 200, 200, 200],
    }, years)
    # No cashflow, no price history -> entire Valuation dimension is n/a.
    ts = score_ticker(_snap(info={"shortName": "S", "sector": "Tech"},
                            income=income, balance=balance), base_settings)
    val = next(d for d in ts.dimensions if d.name.startswith("Valuation"))
    assert all(c.status == "na" for c in val.cells)
    # With the missing dimension weighted as a hard 0, the total would be dragged
    # to ~59. Renormalized over the present dimensions it must clear 70.
    assert ts.total > 70


# --------------------------------------------------------------------------
# #1c Count-based consistency metrics are judged against the history that exists
# for their OWN statement, not the widest statement. Revenue commonly runs more
# years than net income / FCF in yfinance; a clean 4/4 profitable record must
# not be scored against a 10-year revenue window (which branded it a miss).
# --------------------------------------------------------------------------
def test_consistency_counts_use_own_statement_window(base_settings):
    years = [2024, 2023, 2022, 2021]
    ten = list(range(2015, 2025))[::-1]
    income = pd.concat([
        _df({"Total Revenue": [2000 - 50 * i for i in range(10)]}, ten),
        _df({"Net Income": [250, 230, 210, 190]}, years),
    ], sort=True)
    ts = score_ticker(_snap(info={"shortName": "X"}, income=income,
                            balance=_df({"Stockholders Equity": [800] * 4}, years)),
                      base_settings)
    cons = next(d for d in ts.dimensions if d.name == "Consistency")
    yp = next(c for c in cons.cells if c.criterion == "years_profitable")
    fcf = next(c for c in cons.cells if c.criterion == "fcf_positive_years")
    # 4 profitable years out of the 4 net-income years available -> a real (if
    # thin-history-damped) score, not a hard miss against a phantom 10y window.
    assert yp.value == 4.0
    assert yp.label.endswith("(of 4)")
    assert yp.status != "miss"
    # No cash-flow statement at all -> FCF cell is n/a (excluded), not a hard 0.
    assert fcf.status == "na"


# --------------------------------------------------------------------------
# #7 robust (log-linear) growth instead of endpoint CAGR.
# --------------------------------------------------------------------------
def test_trend_growth_clean_exponential():
    assert trend_growth([100, 110, 121, 133.1]) == pytest.approx(0.10, rel=1e-6)


def test_trend_growth_resists_single_endpoint_outlier():
    clean = [100, 110, 121, 133, 146]
    spiked = clean[:-1] + [300]  # last year is a one-off spike
    # Endpoint CAGR would read the spike as the trend; log-linear damps it.
    from warren_bot.analysis.statement_utils import cagr
    assert trend_growth(spiked) < cagr(spiked)


def test_trend_growth_needs_positive_points():
    assert trend_growth([1.0]) is None
    assert trend_growth([-1.0, 2.0, 3.0]) is None


# --------------------------------------------------------------------------
# #10 dividend-yield normalization is consistent and rate-derived when possible.
# --------------------------------------------------------------------------
def test_dividend_yield_prefers_absolute_rate():
    # rate/price is version-independent and disambiguates sub-1% yields.
    assert dividend_yield_pct({"trailingAnnualDividendRate": 2.0,
                               "currentPrice": 100.0}) == pytest.approx(2.0)


@pytest.mark.parametrize("dy,expected", [(0.025, 2.5), (2.5, 2.5)])
def test_dividend_yield_handles_fraction_and_percent(dy, expected):
    assert dividend_yield_pct({"dividendYield": dy}) == pytest.approx(expected)


def test_dividend_yield_none_when_absent():
    assert dividend_yield_pct({}) == 0.0


# --------------------------------------------------------------------------
# #11 currency mismatch suppresses cross-currency metrics instead of emitting
# a wrong number.
# --------------------------------------------------------------------------
def test_currency_mismatch_suppresses_cross_currency_metrics():
    income = _df({"Net Income": [100, 90]}, [2024, 2023])
    cashflow = _df({"Depreciation And Amortization": [10, 10],
                    "Capital Expenditure": [-5, -5]}, [2024, 2023])
    v = compute_valuation(_snap(
        info={"financialCurrency": "JPY", "currency": "USD",
              "currentPrice": 50.0, "marketCap": 1e9, "sharesOutstanding": 1e7,
              "trailingPE": 12.0},
        income=income, cashflow=cashflow))
    assert v.currency_mismatch is True
    assert v.fcf_yield_pct is None
    assert v.margin_of_safety_pct is None


# --------------------------------------------------------------------------
# #3 P/E-vs-history is earnings-based, not the old degenerate price/median.
# --------------------------------------------------------------------------
def test_pe_vs_median_is_earnings_based():
    years = [2024, 2023, 2022, 2021]
    income = _df({"Diluted EPS": [5, 4, 3, 2]}, years)
    # Price = 10x EPS every year, so historical P/E is a flat 10.
    months = pd.date_range("2021-01-31", "2024-12-31", freq="ME")
    price_by_year = {2021: 20.0, 2022: 30.0, 2023: 40.0, 2024: 50.0}
    closes = [price_by_year[d.year] for d in months]
    price_history = pd.DataFrame({"Close": closes}, index=months)
    v = compute_valuation(_snap(
        info={"trailingPE": 10.0, "currentPrice": 50.0,
              "financialCurrency": "USD", "currency": "USD"},
        income=income, price_history=price_history))
    # Earnings-based: current PE 10 / median historical PE 10 = 1.0.
    # The old degenerate price/median-price would have given ~50/35 = 1.43.
    assert v.pe_vs_median_ratio == pytest.approx(1.0, abs=0.05)


# --------------------------------------------------------------------------
# #6 DCF base is normalized over several years (not a single noisy latest year).
# --------------------------------------------------------------------------
def test_owner_earnings_base_is_normalized():
    years = [2024, 2023, 2022]
    income = _df({"Net Income": [100, 100, 100]}, years)
    # Latest capex spike depresses the single latest year's owner earnings.
    cashflow = _df({"Depreciation And Amortization": [20, 20, 20],
                    "Capital Expenditure": [-90, -10, -10]}, years)
    v = compute_valuation(_snap(
        info={"currentPrice": 10.0, "marketCap": 1e6, "sharesOutstanding": 1e4,
              "financialCurrency": "USD", "currency": "USD"},
        income=income, cashflow=cashflow))
    # OE per year: 2022=110, 2023=110, 2024=30. Single-latest would be 30;
    # the normalized 3-year base is (110+110+30)/3 = 83.3.
    assert v.owner_earnings_latest == pytest.approx((110 + 110 + 30) / 3, abs=0.5)


# --------------------------------------------------------------------------
# #5 coverage gating: a high score on thin data can't reach the top tiers.
# --------------------------------------------------------------------------
def _fake_pick(total, coverage):
    score = SimpleNamespace(total=total, data_coverage=coverage, error=None)
    return SimpleNamespace(score=score)


def test_split_picks_gates_on_coverage(base_settings):
    from warren_bot.pipeline import split_picks
    thin = _fake_pick(80, 0.30)     # below min_surface -> dropped
    medium = _fake_pick(80, 0.60)   # >= angle_min but < strong_min -> angle
    full = _fake_pick(80, 0.90)     # strong
    strong, angles, partial = split_picks([thin, medium, full], base_settings)
    assert full in strong and medium not in strong
    assert medium in angles
    assert all(p is not thin for p in strong + angles + partial)
