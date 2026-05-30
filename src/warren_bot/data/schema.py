"""Canonical fundamentals schema — the single source of truth for every data
source's field names.

The rest of the codebase consumes statements one way: a DataFrame whose rows are
line items (matched by alias via :func:`analysis.statement_utils.row`) and whose
columns are fiscal-period-end dates. Rather than invent a parallel model and
rewrite every consumer, we declare that existing shape the canonical contract and
make *every* source — yfinance, SEC EDGAR, FMP — normalize INTO it.

This module is where the mapping lives. For each canonical metric we record:
  - ``label``    : the DataFrame index label (also the primary yfinance alias)
  - ``statement``: which statement it belongs to (income / balance / cashflow)
  - ``yf``       : yfinance row-label aliases (first match wins)
  - ``edgar``    : us-gaap XBRL tags (first present wins)
  - ``fmp``      : Financial Modeling Prep JSON field names
  - ``negate_edgar`` / ``negate_fmp``: flip sign to match yfinance's convention
    (e.g. yfinance reports capex as a negative cash outflow; EDGAR's
    ``PaymentsToAcquire...`` is a positive number).

Because the canonical label IS the primary yfinance alias, statements produced by
the EDGAR/FMP adapters are matched by the exact same ``statement_utils.row()``
alias logic the analysis modules already use — nothing downstream changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

INCOME = "income"
BALANCE = "balance"
CASHFLOW = "cashflow"


@dataclass(frozen=True)
class Metric:
    label: str                      # canonical DataFrame index label
    statement: str                  # INCOME | BALANCE | CASHFLOW
    yf: list[str] = field(default_factory=list)
    edgar: list[str] = field(default_factory=list)
    fmp: list[str] = field(default_factory=list)
    negate_edgar: bool = False
    negate_fmp: bool = False

    @property
    def aliases(self) -> list[str]:
        """All accepted row labels — the canonical label first, then the rest."""
        out = [self.label]
        out += [a for a in self.yf if a != self.label]
        return out


# --- The registry -----------------------------------------------------------
# Ordered roughly the way the statements read. The canonical labels match the
# aliases historically defined in analysis/ratios.py and analysis/valuation.py.
METRICS: list[Metric] = [
    # ---- Income statement ----
    Metric("Total Revenue", INCOME,
           yf=["Total Revenue", "TotalRevenue", "Revenue"],
           edgar=["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                  "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
           fmp=["revenue"]),
    Metric("Gross Profit", INCOME,
           yf=["Gross Profit", "GrossProfit"],
           edgar=["GrossProfit"], fmp=["grossProfit"]),
    Metric("Cost Of Revenue", INCOME,
           yf=["Cost Of Revenue", "CostOfRevenue", "Cost of Revenue"],
           edgar=["CostOfRevenue", "CostOfGoodsAndServicesSold"], fmp=["costOfRevenue"]),
    Metric("Operating Income", INCOME,
           yf=["Operating Income", "OperatingIncome", "EBIT"],
           edgar=["OperatingIncomeLoss"], fmp=["operatingIncome"]),
    Metric("Net Income", INCOME,
           yf=["Net Income", "Net Income Common Stockholders", "NetIncome"],
           edgar=["NetIncomeLoss", "ProfitLoss"], fmp=["netIncome"]),
    Metric("Interest Expense", INCOME,
           yf=["Interest Expense", "InterestExpense"],
           edgar=["InterestExpense", "InterestExpenseNonoperating"], fmp=["interestExpense"]),
    Metric("Tax Provision", INCOME,
           yf=["Tax Provision", "Income Tax Expense"],
           edgar=["IncomeTaxExpenseBenefit"], fmp=["incomeTaxExpense"]),
    Metric("Pretax Income", INCOME,
           yf=["Pretax Income", "Income Before Tax"],
           edgar=["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                  "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
           fmp=["incomeBeforeTax"]),
    Metric("Diluted EPS", INCOME,
           yf=["Diluted EPS", "Basic EPS", "EPS"],
           edgar=["EarningsPerShareDiluted", "EarningsPerShareBasic"],
           fmp=["epsdiluted", "eps"]),

    # ---- Balance sheet ----
    Metric("Total Assets", BALANCE,
           yf=["Total Assets", "TotalAssets"], edgar=["Assets"], fmp=["totalAssets"]),
    Metric("Stockholders Equity", BALANCE,
           yf=["Stockholders Equity", "Total Equity Gross Minority Interest",
               "Common Stock Equity"],
           edgar=["StockholdersEquity",
                  "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
           fmp=["totalStockholdersEquity"]),
    Metric("Long Term Debt", BALANCE,
           yf=["Long Term Debt", "LongTermDebt"],
           edgar=["LongTermDebtNoncurrent", "LongTermDebt"], fmp=["longTermDebt"]),
    Metric("Current Debt", BALANCE,
           yf=["Current Debt", "Short Long Term Debt", "ShortTermDebt"],
           edgar=["LongTermDebtCurrent", "DebtCurrent"], fmp=["shortTermDebt"]),
    Metric("Current Assets", BALANCE,
           yf=["Current Assets", "Total Current Assets"],
           edgar=["AssetsCurrent"], fmp=["totalCurrentAssets"]),
    Metric("Current Liabilities", BALANCE,
           yf=["Current Liabilities", "Total Current Liabilities"],
           edgar=["LiabilitiesCurrent"], fmp=["totalCurrentLiabilities"]),
    Metric("Cash And Cash Equivalents", BALANCE,
           yf=["Cash And Cash Equivalents",
               "Cash Cash Equivalents And Short Term Investments"],
           edgar=["CashAndCashEquivalentsAtCarryingValue"],
           fmp=["cashAndCashEquivalents"]),
    Metric("Ordinary Shares Number", BALANCE,
           yf=["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"],
           edgar=["CommonStockSharesOutstanding", "CommonStockSharesIssued"],
           fmp=["weightedAverageShsOutDil", "weightedAverageShsOut"]),

    # ---- Cash flow ----
    Metric("Operating Cash Flow", CASHFLOW,
           yf=["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
           edgar=["NetCashProvidedByUsedInOperatingActivities",
                  "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
           fmp=["operatingCashFlow", "netCashProvidedByOperatingActivities"]),
    Metric("Capital Expenditure", CASHFLOW,
           yf=["Capital Expenditure", "CapitalExpenditure"],
           edgar=["PaymentsToAcquirePropertyPlantAndEquipment",
                  "PaymentsToAcquireProductiveAssets"],
           fmp=["capitalExpenditure"],
           negate_edgar=True),  # EDGAR reports the outflow as positive; yfinance is negative
    Metric("Depreciation And Amortization", CASHFLOW,
           yf=["Depreciation And Amortization", "Depreciation Amortization Depletion",
               "Depreciation"],
           edgar=["DepreciationDepletionAndAmortization",
                  "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization"],
           fmp=["depreciationAndAmortization"]),
    Metric("Free Cash Flow", CASHFLOW,
           yf=["Free Cash Flow", "FreeCashFlow"], edgar=[], fmp=["freeCashFlow"]),
]

# Derived lookups -------------------------------------------------------------
BY_LABEL: dict[str, Metric] = {m.label: m for m in METRICS}
INCOME_ROWS = [m.label for m in METRICS if m.statement == INCOME]
BALANCE_ROWS = [m.label for m in METRICS if m.statement == BALANCE]
CASHFLOW_ROWS = [m.label for m in METRICS if m.statement == CASHFLOW]
ROWS_BY_STATEMENT = {INCOME: INCOME_ROWS, BALANCE: BALANCE_ROWS, CASHFLOW: CASHFLOW_ROWS}

# Alias lists consumed by analysis/ratios.py and analysis/valuation.py so the
# de-facto schema is defined here once instead of duplicated across modules.
ALIASES: dict[str, list[str]] = {m.label: m.aliases for m in METRICS}


def _normalize(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


_NORM_TO_LABEL: dict[str, str] = {}
for _m in METRICS:
    for _alias in _m.aliases:
        _NORM_TO_LABEL.setdefault(_normalize(_alias), _m.label)


def canonical_label(raw_label: str) -> str | None:
    """Map an arbitrary statement row label to its canonical label, or None."""
    return _NORM_TO_LABEL.get(_normalize(raw_label))


def build_statement(rows: dict[str, pd.Series]) -> pd.DataFrame | None:
    """Assemble a canonical statement DataFrame from ``{canonical_label: series}``.

    Each series is indexed by fiscal-period-end ``pd.Timestamp`` and the result
    has canonical labels on the row axis and dates on the column axis (most
    recent last), matching the orientation produced by yfinance after the
    analysis layer reads it. Empty input returns None so callers treat it as a
    missing statement.
    """
    clean = {label: s for label, s in rows.items() if s is not None and len(s) > 0}
    if not clean:
        return None
    df = pd.DataFrame(clean).T          # rows = labels, columns = dates
    df = df.reindex(sorted(df.columns), axis=1)
    return df
