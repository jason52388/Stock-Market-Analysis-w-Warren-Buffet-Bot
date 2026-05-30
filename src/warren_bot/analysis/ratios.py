"""Profitability and balance-sheet ratios computed from a TickerSnapshot."""
from __future__ import annotations

from dataclasses import dataclass

import math

from ..data.fetcher import TickerSnapshot
from ..data.schema import ALIASES
from .statement_utils import aligned, avg, frame, row

# Line-item aliases live in data.schema (the single source of truth shared with
# the EDGAR/FMP adapters). These names are kept for readability at call sites.
# `_TOTAL_DEBT` has no schema metric (it's derived from long+short term debt).
_REVENUE = ALIASES["Total Revenue"]
_GROSS_PROFIT = ALIASES["Gross Profit"]
_COST_OF_REVENUE = ALIASES["Cost Of Revenue"]
_OPERATING_INCOME = ALIASES["Operating Income"]
_NET_INCOME = ALIASES["Net Income"]
_INTEREST_EXPENSE = ALIASES["Interest Expense"]
_TAX_PROVISION = ALIASES["Tax Provision"]
_PRETAX_INCOME = ALIASES["Pretax Income"]

_TOTAL_ASSETS = ALIASES["Total Assets"]
_TOTAL_EQUITY = ALIASES["Stockholders Equity"]
_TOTAL_DEBT = ["Total Debt", "TotalDebt"]
_LONG_TERM_DEBT = ALIASES["Long Term Debt"]
_SHORT_TERM_DEBT = ALIASES["Current Debt"]
_CURRENT_ASSETS = ALIASES["Current Assets"]
_CURRENT_LIAB = ALIASES["Current Liabilities"]
_CASH = ALIASES["Cash And Cash Equivalents"]
_SHARES = ALIASES["Ordinary Shares Number"]

_CFO = ALIASES["Operating Cash Flow"]
_CAPEX = ALIASES["Capital Expenditure"]
_DEPRECIATION = ALIASES["Depreciation And Amortization"]
_FCF = ALIASES["Free Cash Flow"]


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


def _total_debt_row(balance):
    """Total debt row, synthesized from long- + short-term debt when the
    consolidated 'Total Debt' line is absent. Aligned on dates, not position."""
    td = row(balance, _TOTAL_DEBT)
    if td is not None:
        return td
    lt = row(balance, _LONG_TERM_DEBT)
    st = row(balance, _SHORT_TERM_DEBT)
    f = frame(lt=lt, st=st)
    if f.empty:
        return lt if lt is not None else st
    # Sum what's available per date (a missing leg counts as 0).
    return f.fillna(0.0).sum(axis=1)


def compute_ratios(snap: TickerSnapshot) -> RatioSet:
    income, balance = snap.income, snap.balance

    net_row = row(income, _NET_INCOME)
    rev_row = row(income, _REVENUE)
    gross_row = row(income, _GROSS_PROFIT)
    if gross_row is None:
        cost_row = row(income, _COST_OF_REVENUE)
        if cost_row is not None and rev_row is not None:
            gross_row = rev_row - cost_row
    equity_row = row(balance, _TOTAL_EQUITY)
    debt_row = _total_debt_row(balance)
    ebit_row = row(income, _OPERATING_INCOME)
    tax_row = row(income, _TAX_PROVISION)
    pretax_row = row(income, _PRETAX_INCOME)
    int_row = row(income, _INTEREST_EXPENSE)
    cash_row = row(balance, _CASH)

    # ROE = NI / Equity, paired by fiscal year (date-aligned). Undefined when
    # equity <= 0 (negative book equity makes the ratio meaningless, not great).
    roe: list[float | None] = []
    f = aligned(frame(net=net_row, eq=equity_row), "net", "eq")
    for r in f.itertuples():
        roe.append(r.net * 100.0 / r.eq if r.eq and r.eq > 0 else None)

    # Margins, date-aligned to the matching year's revenue.
    gm: list[float | None] = []
    f = aligned(frame(gross=gross_row, rev=rev_row), "gross", "rev")
    for r in f.itertuples():
        gm.append(r.gross * 100.0 / r.rev if r.rev else None)
    nm: list[float | None] = []
    f = aligned(frame(net=net_row, rev=rev_row), "net", "rev")
    for r in f.itertuples():
        nm.append(r.net * 100.0 / r.rev if r.rev else None)

    # ROIC = NOPAT / Invested Capital.
    #   Invested Capital = Equity + Total Debt  (total-capital basis).
    # We deliberately do NOT subtract cash: subtracting *all* cash blows up or
    # turns negative for net-cash companies (Apple, Alphabet, ...), which then
    # silently drop out of the average and bias it. Total-capital ROIC slightly
    # understates the cash-rich (conservative) but never explodes or flips sign.
    roic: list[float | None] = []
    f = aligned(frame(ebit=ebit_row, tax=tax_row, pretax=pretax_row,
                      eq=equity_row, debt=debt_row),
                require=["ebit", "eq", "debt"])
    for r in f.itertuples():
        pretax = getattr(r, "pretax", math.nan)
        tax = getattr(r, "tax", math.nan)
        eff_tax = (tax / pretax) if (pretax and not math.isnan(pretax)
                                     and not math.isnan(tax)) else None
        if eff_tax is None or eff_tax < 0 or eff_tax > 1:
            eff_tax = 0.21  # US statutory fallback for missing/nonsensical rates
        nopat = r.ebit * (1 - eff_tax)
        invested = r.eq + r.debt
        roic.append(nopat * 100.0 / invested if invested and invested > 0 else None)

    # --- Latest balance-sheet ratios (most recent year with both legs) ---
    de = None
    f = aligned(frame(debt=debt_row, eq=equity_row), "debt", "eq")
    if not f.empty:
        last = f.iloc[-1]
        # Negative/zero equity -> D/E is meaningless; leave as None so it shows
        # 'n/a' rather than a spuriously "excellent" (negative) ratio.
        de = (last["debt"] / last["eq"]) if last["eq"] and last["eq"] > 0 else None

    cur_ratio = None
    f = aligned(frame(ca=row(balance, _CURRENT_ASSETS), cl=row(balance, _CURRENT_LIAB)),
                "ca", "cl")
    if not f.empty:
        last = f.iloc[-1]
        cur_ratio = (last["ca"] / last["cl"]) if last["cl"] else None

    icov = None
    f = aligned(frame(ebit=ebit_row, intr=int_row), "ebit", "intr")
    if not f.empty:
        last = f.iloc[-1]
        denom = abs(last["intr"])
        icov = (last["ebit"] / denom) if denom else None

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
