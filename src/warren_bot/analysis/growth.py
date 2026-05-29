"""Growth and consistency metrics."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.fetcher import TickerSnapshot
from .ratios import _CFO, _FCF, _CAPEX, _SHARES, _NET_INCOME, _REVENUE
from .statement_utils import cagr, row, series_values


@dataclass
class GrowthSet:
    revenue_cagr_pct: float | None
    eps_cagr_pct: float | None              # we use net income as a proxy when EPS missing
    years_in_window: int
    years_profitable: int                   # net income > 0 in last N
    fcf_positive_years: int
    share_count_cagr_pct: float | None      # negative = buybacks


def _eps_proxy_series(snap: TickerSnapshot) -> list[float]:
    """Prefer EPS if available, otherwise net income (proxy for per-share growth)."""
    income = snap.income
    eps = row(income, ["Diluted EPS", "Basic EPS", "EPS"])
    eps_vals = series_values(eps)
    if eps_vals:
        return eps_vals
    return series_values(row(income, _NET_INCOME))


def compute_growth(snap: TickerSnapshot) -> GrowthSet:
    rev = series_values(row(snap.income, _REVENUE))
    eps = _eps_proxy_series(snap)
    net = series_values(row(snap.income, _NET_INCOME))

    fcf_direct = series_values(row(snap.cashflow, _FCF))
    if fcf_direct:
        fcf = fcf_direct
    else:
        cfo = series_values(row(snap.cashflow, _CFO))
        capex = series_values(row(snap.cashflow, _CAPEX))
        n = min(len(cfo), len(capex))
        # capex is reported negative; FCF = CFO + capex (which subtracts since capex<0)
        fcf = [cfo[i] + capex[i] for i in range(n)]

    shares = series_values(row(snap.balance, _SHARES))

    return GrowthSet(
        revenue_cagr_pct=(cagr(rev) * 100.0) if cagr(rev) is not None else None,
        eps_cagr_pct=(cagr(eps) * 100.0) if cagr(eps) is not None else None,
        years_in_window=max(len(rev), len(net), len(fcf)),
        years_profitable=sum(1 for v in net if v is not None and v > 0),
        fcf_positive_years=sum(1 for v in fcf if v is not None and v > 0),
        share_count_cagr_pct=(cagr(shares) * 100.0) if cagr(shares) is not None else None,
    )
