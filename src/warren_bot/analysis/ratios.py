"""Profitability and balance-sheet ratios computed from a TickerSnapshot."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.fetcher import TickerSnapshot
from .statement_utils import avg, latest, row, safe_div, series_values

# yfinance line-item aliases. First match wins.
_REVENUE = ["Total Revenue", "TotalRevenue", "Revenue"]
_GROSS_PROFIT = ["Gross Profit", "GrossProfit"]
_COST_OF_REVENUE = ["Cost Of Revenue", "CostOfRevenue", "Cost of Revenue"]
_OPERATING_INCOME = ["Operating Income", "OperatingIncome", "EBIT"]
_NET_INCOME = ["Net Income", "Net Income Common Stockholders", "NetIncome"]
_INTEREST_EXPENSE = ["Interest Expense", "InterestExpense"]
_TAX_PROVISION = ["Tax Provision", "Income Tax Expense"]
_PRETAX_INCOME = ["Pretax Income", "Income Before Tax"]

_TOTAL_ASSETS = ["Total Assets", "TotalAssets"]
_TOTAL_EQUITY = [
    "Stockholders Equity",
    "Total Equity Gross Minority Interest",
    "Common Stock Equity",
]
_TOTAL_DEBT = ["Total Debt", "TotalDebt"]
_LONG_TERM_DEBT = ["Long Term Debt", "LongTermDebt"]
_SHORT_TERM_DEBT = ["Current Debt", "Short Long Term Debt", "ShortTermDebt"]
_CURRENT_ASSETS = ["Current Assets", "Total Current Assets"]
_CURRENT_LIAB = ["Current Liabilities", "Total Current Liabilities"]
_CASH = ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]
_SHARES = ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"]

_CFO = ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]
_CAPEX = ["Capital Expenditure", "CapitalExpenditure"]
_DEPRECIATION = [
    "Depreciation And Amortization",
    "Depreciation Amortization Depletion",
    "Depreciation",
]
_FCF = ["Free Cash Flow", "FreeCashFlow"]


@dataclass
class RatioSet:
    # Per-year series (oldest first) where useful
    roe_pct_series: list[float | None]
    roic_pct_series: list[float | None]
    gross_margin_pct_series: list[float | None]
    net_margin_pct_series: list[float | None]

    # Headline (10yr-or-available averages, in percent)
    roe_pct_avg: float | None
    roic_pct_avg: float | None
    gross_margin_pct_avg: float | None
    net_margin_pct_avg: float | None

    # Latest balance-sheet snapshot
    debt_to_equity: float | None
    interest_coverage: float | None
    current_ratio: float | None


def _aligned_series(num: list[float], den: list[float]) -> list[float | None]:
    """Element-wise ratio, padded so output length matches the longer side."""
    n = min(len(num), len(den))
    return [safe_div(num[i], den[i]) for i in range(n)]


def compute_ratios(snap: TickerSnapshot) -> RatioSet:
    income, balance = snap.income, snap.balance

    rev_s = series_values(row(income, _REVENUE))
    net_s = series_values(row(income, _NET_INCOME))
    gross_row = row(income, _GROSS_PROFIT)
    if gross_row is None:
        cost_row = row(income, _COST_OF_REVENUE)
        if cost_row is not None and row(income, _REVENUE) is not None:
            gross_row = row(income, _REVENUE) - cost_row
    gross_s = series_values(gross_row) if gross_row is not None else []

    equity_s = series_values(row(balance, _TOTAL_EQUITY))
    total_debt_s = series_values(row(balance, _TOTAL_DEBT))
    if not total_debt_s:
        lt = series_values(row(balance, _LONG_TERM_DEBT))
        st = series_values(row(balance, _SHORT_TERM_DEBT))
        n = min(len(lt), len(st))
        total_debt_s = [lt[i] + st[i] for i in range(n)] if n else lt or st

    ebit_s = series_values(row(income, _OPERATING_INCOME))
    tax_s = series_values(row(income, _TAX_PROVISION))
    pretax_s = series_values(row(income, _PRETAX_INCOME))
    int_exp_s = series_values(row(income, _INTEREST_EXPENSE))

    # Series-level ratios (in percent)
    roe = [
        safe_div(net_s[i] * 100.0, equity_s[i]) if equity_s[i] and equity_s[i] > 0 else None
        for i in range(min(len(net_s), len(equity_s)))
    ]
    gm = _aligned_series([g * 100.0 for g in gross_s], rev_s)
    nm = _aligned_series([n_ * 100.0 for n_ in net_s], rev_s)

    # ROIC = NOPAT / (Equity + Total Debt - Cash)
    cash_s = series_values(row(balance, _CASH))
    n_align = min(len(ebit_s), len(tax_s), len(pretax_s), len(equity_s), len(total_debt_s))
    roic: list[float | None] = []
    for i in range(n_align):
        eff_tax = safe_div(tax_s[i], pretax_s[i]) if pretax_s[i] else None
        if eff_tax is None or eff_tax < 0:
            eff_tax = 0.21  # fall back to US statutory if missing/odd
        nopat = ebit_s[i] * (1 - eff_tax)
        cash = cash_s[i] if i < len(cash_s) else 0
        invested = equity_s[i] + total_debt_s[i] - cash
        roic.append(safe_div(nopat * 100.0, invested) if invested and invested > 0 else None)

    # Latest balance-sheet ratios. Bind the rows once so `latest()` doesn't
    # re-execute the row lookup (and its internal sort) twice per ticker.
    cur_assets_row = row(balance, _CURRENT_ASSETS)
    cur_liab_row = row(balance, _CURRENT_LIAB)
    cur_ratio = safe_div(latest(cur_assets_row), latest(cur_liab_row))

    debt_latest = total_debt_s[-1] if total_debt_s else None
    equity_latest = equity_s[-1] if equity_s else None
    de = safe_div(debt_latest, equity_latest)

    # Interest coverage uses latest EBIT/|Interest|
    ebit_latest = ebit_s[-1] if ebit_s else None
    int_latest = abs(int_exp_s[-1]) if int_exp_s else None
    icov = safe_div(ebit_latest, int_latest) if int_latest else None

    return RatioSet(
        roe_pct_series=roe,
        roic_pct_series=roic,
        gross_margin_pct_series=gm,
        net_margin_pct_series=nm,
        roe_pct_avg=avg(roe),
        roic_pct_avg=avg(roic),
        gross_margin_pct_avg=avg(gm),
        net_margin_pct_avg=avg(nm),
        debt_to_equity=de,
        interest_coverage=icov,
        current_ratio=cur_ratio,
    )
