"""Buffett-flavoured valuation: FCF yield, P/E vs 10yr median, simple owner-earnings DCF."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.fetcher import TickerSnapshot
from .ratios import _CFO, _CAPEX, _DEPRECIATION, _NET_INCOME
from .statement_utils import row, safe_div, series_values


@dataclass
class ValuationSet:
    price: float | None
    market_cap: float | None
    pe_ratio: float | None
    pe_median_10y: float | None
    pe_vs_median_ratio: float | None         # current / median; <1 = cheap vs history
    fcf_yield_pct: float | None
    owner_earnings_latest: float | None
    intrinsic_value_per_share: float | None
    margin_of_safety_pct: float | None
    shareholder_yield_pct: float | None       # dividend + buyback


def _safe_get(info: dict, *keys, default=None):
    for k in keys:
        v = info.get(k)
        if v is not None:
            return v
    return default


def compute_valuation(snap: TickerSnapshot) -> ValuationSet:
    info = snap.info or {}
    price = _safe_get(info, "currentPrice", "regularMarketPrice", "previousClose")
    market_cap = info.get("marketCap")
    pe = _safe_get(info, "trailingPE", "forwardPE")
    dividend_yield = info.get("dividendYield") or 0.0
    if dividend_yield and dividend_yield > 1:
        # Some yfinance versions return % already (e.g. 2.5 vs 0.025); normalize to fraction
        dividend_yield = dividend_yield / 100.0

    # P/E vs 10yr median: approximated from monthly price history * trailing EPS (constant proxy)
    # yfinance doesn't ship historical PE directly; we approximate by treating EPS as latest and
    # comparing price multiples. It's directional only — flagged as such in the thesis.
    pe_median = None
    pe_vs_median = None
    if snap.price_history is not None and not snap.price_history.empty and pe and price:
        # price * (pe/price) is identity; we use price history relative to current
        median_price = float(snap.price_history["Close"].median())
        if median_price and price:
            pe_median = pe * (median_price / price)
            pe_vs_median = safe_div(pe, pe_median)

    # Owner earnings = Net Income + D&A - Maintenance Capex (we proxy maintenance capex = total capex)
    ni = series_values(row(snap.income, _NET_INCOME))
    da = series_values(row(snap.cashflow, _DEPRECIATION))
    capex = series_values(row(snap.cashflow, _CAPEX))
    cfo = series_values(row(snap.cashflow, _CFO))

    oe_series: list[float] = []
    n = min(len(ni), len(da), len(capex))
    for i in range(n):
        # capex is negative; adding it subtracts maintenance reinvestment
        oe_series.append(ni[i] + da[i] + capex[i])
    if not oe_series and cfo and capex:
        m = min(len(cfo), len(capex))
        oe_series = [cfo[i] + capex[i] for i in range(m)]

    oe_latest = oe_series[-1] if oe_series else None

    # Simple 2-stage DCF on owner earnings:
    #   stage 1: 5 years growth at min(eps_cagr, 10%), then terminal growth 2.5%, discount 10%.
    intrinsic_per_share = None
    mos = None
    if oe_latest and oe_latest > 0:
        # growth proxy: 5-year owner-earnings CAGR, capped
        if len(oe_series) >= 2 and oe_series[0] > 0:
            n_yrs = len(oe_series) - 1
            g = (oe_series[-1] / oe_series[0]) ** (1 / n_yrs) - 1
        else:
            g = 0.05
        g = max(min(g, 0.10), -0.02)
        discount = 0.10
        terminal_g = 0.025

        pv = 0.0
        oe_proj = oe_latest
        for year in range(1, 6):
            oe_proj *= 1 + g
            pv += oe_proj / (1 + discount) ** year
        terminal = oe_proj * (1 + terminal_g) / (discount - terminal_g)
        pv += terminal / (1 + discount) ** 5

        shares_out = info.get("sharesOutstanding")
        if shares_out and shares_out > 0:
            intrinsic_per_share = pv / shares_out
            if price:
                mos = (intrinsic_per_share - price) / intrinsic_per_share * 100.0

    fcf_yield = None
    if market_cap and market_cap > 0 and oe_latest:
        fcf_yield = oe_latest / market_cap * 100.0

    # Shareholder yield = dividend yield + net buyback yield (share count shrink, %)
    shareholder_yield = None
    sh_series = series_values(row(snap.balance, ["Ordinary Shares Number", "Share Issued"]))
    buyback_yield = 0.0
    if len(sh_series) >= 2 and sh_series[0] and sh_series[-1]:
        change = (sh_series[-1] - sh_series[0]) / sh_series[0]
        yrs = max(len(sh_series) - 1, 1)
        buyback_yield = -change / yrs * 100.0  # negative change -> positive yield
    shareholder_yield = (dividend_yield * 100.0) + buyback_yield

    return ValuationSet(
        price=float(price) if price else None,
        market_cap=float(market_cap) if market_cap else None,
        pe_ratio=float(pe) if pe else None,
        pe_median_10y=pe_median,
        pe_vs_median_ratio=pe_vs_median,
        fcf_yield_pct=fcf_yield,
        owner_earnings_latest=oe_latest,
        intrinsic_value_per_share=intrinsic_per_share,
        margin_of_safety_pct=mos,
        shareholder_yield_pct=shareholder_yield,
    )
