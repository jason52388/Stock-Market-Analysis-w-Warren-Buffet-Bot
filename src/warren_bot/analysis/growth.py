"""Growth and consistency metrics."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.fetcher import TickerSnapshot
from .ratios import _CFO, _FCF, _CAPEX, _SHARES, _NET_INCOME, _REVENUE
from .statement_utils import aligned, frame, row, series_values, trend_growth


@dataclass
class GrowthSet:
    revenue_cagr_pct: float | None
    eps_cagr_pct: float | None              # we use net income as a proxy when EPS missing
    years_in_window: int                    # widest statement history available
    years_profitable: int                   # net income > 0 in last N
    fcf_positive_years: int
    share_count_cagr_pct: float | None      # negative = buybacks
    net_years: int = 0                      # # of usable net-income years
    fcf_years: int = 0                      # # of usable FCF years


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
        # Date-align CFO and capex before summing so a missing year on one side
        # can't shift the pairing. capex is reported negative; FCF = CFO + capex.
        f = aligned(frame(cfo=row(snap.cashflow, _CFO), capex=row(snap.cashflow, _CAPEX)),
                    require=["cfo", "capex"])
        fcf = [r.cfo + r.capex for r in f.itertuples()]

    shares = series_values(row(snap.balance, _SHARES))

    rev_g = trend_growth(rev)
    eps_g = trend_growth(eps)
    sh_g = trend_growth(shares)
    return GrowthSet(
        revenue_cagr_pct=(rev_g * 100.0) if rev_g is not None else None,
        eps_cagr_pct=(eps_g * 100.0) if eps_g is not None else None,
        years_in_window=max(len(rev), len(net), len(fcf)),
        years_profitable=sum(1 for v in net if v is not None and v > 0),
        fcf_positive_years=sum(1 for v in fcf if v is not None and v > 0),
        share_count_cagr_pct=(sh_g * 100.0) if sh_g is not None else None,
        # Count-based consistency metrics must be judged against the history that
        # actually exists for *their own* statement, not the widest window across
        # all statements. Revenue often runs longer than net income / FCF in
        # yfinance; scoring "4 profitable years" against a 10-year revenue window
        # wrongly brands a clean 4/4 record a miss.
        net_years=len(net),
        fcf_years=len(fcf),
    )
