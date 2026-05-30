"""Buffett-flavoured valuation: FCF yield, P/E vs history, simple owner-earnings DCF."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.fetcher import TickerSnapshot
from ..data.schema import ALIASES
from .ratios import _CFO, _CAPEX, _DEPRECIATION, _NET_INCOME, _SHARES

_EPS = ALIASES["Diluted EPS"]   # ["Diluted EPS", "Basic EPS", "EPS"]
from .statement_utils import (
    aligned,
    dividend_yield_pct,
    frame,
    row,
    series_values,
    trend_growth,
)


@dataclass
class ValuationSet:
    price: float | None
    market_cap: float | None
    pe_ratio: float | None
    pe_vs_median_ratio: float | None         # current / median; <1 = cheap vs history
    fcf_yield_pct: float | None
    owner_earnings_latest: float | None       # normalized base used for DCF / yield
    intrinsic_value_per_share: float | None
    margin_of_safety_pct: float | None
    shareholder_yield_pct: float | None       # dividend + buyback
    currency_mismatch: bool = False           # statements vs quote currency differ


# DCF assumptions (kept explicit so they're easy to audit/tune).
_DISCOUNT_RATE = 0.10
_TERMINAL_GROWTH = 0.025
_GROWTH_CAP = 0.10
_GROWTH_FLOOR = -0.02
_PROJECTION_YEARS = 5
_NORMALIZE_YEARS = 3   # owner-earnings averaging window for the DCF base


def _safe_get(info: dict, *keys, default=None):
    for k in keys:
        v = info.get(k)
        if v is not None:
            return v
    return default


def _owner_earnings(snap: TickerSnapshot) -> list[float]:
    """Owner earnings per fiscal year (oldest-first), date-aligned across the
    income and cash-flow statements.  OE = Net Income + D&A + Capex (capex is
    reported negative, so it subtracts reinvestment)."""
    f = aligned(
        frame(ni=row(snap.income, _NET_INCOME),
              da=row(snap.cashflow, _DEPRECIATION),
              capex=row(snap.cashflow, _CAPEX)),
        require=["ni", "da", "capex"],
    )
    oe = [r.ni + r.da + r.capex for r in f.itertuples()]
    if oe:
        return oe
    # Fallback: CFO + capex when D&A is unavailable.
    f = aligned(frame(cfo=row(snap.cashflow, _CFO), capex=row(snap.cashflow, _CAPEX)),
                require=["cfo", "capex"])
    return [r.cfo + r.capex for r in f.itertuples()]


def _historical_pe_ratio(snap: TickerSnapshot, current_pe: float | None,
                         current_price: float | None) -> float | None:
    """current P/E ÷ median historical P/E, where the historical series is built
    from *actual* per-year earnings (EPS = NI/shares) priced at each fiscal-year
    end.  This is a genuine earnings-based multiple history — unlike the old
    proxy, which algebraically collapsed to price ÷ median-price and ignored
    earnings entirely.  Returns None if there isn't enough history."""
    if not current_pe or current_pe <= 0:
        return None
    hist = snap.price_history
    if hist is None or hist.empty or "Close" not in hist:
        return None
    # Per-year EPS: prefer reported diluted/basic EPS, else NI / shares.
    eps_row = row(snap.income, _EPS)
    f = frame(eps=eps_row)
    if f.empty:
        f = aligned(frame(ni=row(snap.income, _NET_INCOME),
                          sh=row(snap.income, _SHARES) if row(snap.income, _SHARES) is not None
                          else row(snap.balance, _SHARES)),
                    require=["ni", "sh"])
        if f.empty:
            return None
        f = f.assign(eps=f["ni"] / f["sh"])
    try:
        close = hist["Close"].dropna()
        idx = close.index
        if getattr(idx, "tz", None) is not None:
            close = close.copy()
            close.index = idx.tz_localize(None)
        close = close.sort_index()
        pes: list[float] = []
        for ts, r in f.iterrows():
            eps = float(r["eps"])
            if eps <= 0:
                continue
            ts_naive = ts.tz_localize(None) if getattr(ts, "tzinfo", None) else ts
            px = close.asof(ts_naive)  # last close at/before the fiscal-year end
            if px is None or px != px:  # NaN guard
                continue
            pes.append(float(px) / eps)
        if len(pes) < 2:
            return None
        pes.sort()
        mid = len(pes) // 2
        median_pe = pes[mid] if len(pes) % 2 else (pes[mid - 1] + pes[mid]) / 2
        if median_pe <= 0:
            return None
        return current_pe / median_pe
    except Exception:
        return None


def compute_valuation(snap: TickerSnapshot) -> ValuationSet:
    info = snap.info or {}
    price = _safe_get(info, "currentPrice", "regularMarketPrice", "previousClose")
    market_cap = info.get("marketCap")
    pe = _safe_get(info, "trailingPE", "forwardPE")

    # Currency consistency: statements are in `financialCurrency`, price/market
    # cap in `currency`. When they differ (many ADRs / foreign listings), any
    # metric that mixes the two (FCF yield, intrinsic value vs price, earnings-
    # based P/E) would be silently wrong — so we compute them as None rather than
    # emit a bogus number. Share-count and dividend-rate yields stay valid.
    fin_ccy = info.get("financialCurrency")
    quote_ccy = info.get("currency")
    currency_mismatch = bool(fin_ccy and quote_ccy and fin_ccy != quote_ccy)

    dividend_yield = dividend_yield_pct(info)  # already in percent

    pe_vs_median = None if currency_mismatch else _historical_pe_ratio(snap, pe, price)

    oe_series = _owner_earnings(snap)
    # Normalize the DCF/yield base over the last few years so one noisy capex or
    # one-off earnings year doesn't swing the whole valuation.
    oe_latest = None
    if oe_series:
        window = oe_series[-_NORMALIZE_YEARS:]
        oe_latest = sum(window) / len(window)

    intrinsic_per_share = None
    mos = None
    if oe_latest and oe_latest > 0 and not currency_mismatch:
        g = trend_growth(oe_series)          # robust log-linear growth
        if g is None:
            g = 0.05
        g = max(min(g, _GROWTH_CAP), _GROWTH_FLOOR)

        pv = 0.0
        oe_proj = oe_latest
        for year in range(1, _PROJECTION_YEARS + 1):
            oe_proj *= 1 + g
            pv += oe_proj / (1 + _DISCOUNT_RATE) ** year
        terminal = oe_proj * (1 + _TERMINAL_GROWTH) / (_DISCOUNT_RATE - _TERMINAL_GROWTH)
        pv += terminal / (1 + _DISCOUNT_RATE) ** _PROJECTION_YEARS

        shares_out = info.get("sharesOutstanding")
        if shares_out and shares_out > 0:
            intrinsic_per_share = pv / shares_out
            if price:
                mos = (intrinsic_per_share - price) / intrinsic_per_share * 100.0

    fcf_yield = None
    if market_cap and market_cap > 0 and oe_latest and not currency_mismatch:
        fcf_yield = oe_latest / market_cap * 100.0

    # Shareholder yield = dividend yield + net buyback yield. Buyback yield is
    # the annualized rate of share-count shrinkage (robust trend, not endpoints);
    # both legs are unitless/percent so currency mismatch doesn't affect them.
    sh_series = series_values(row(snap.balance, _SHARES))
    buyback_yield = 0.0
    g_sh = trend_growth(sh_series)
    if g_sh is not None:
        buyback_yield = -g_sh * 100.0  # shrinking share count -> positive yield
    shareholder_yield = dividend_yield + buyback_yield

    return ValuationSet(
        price=float(price) if price else None,
        market_cap=float(market_cap) if market_cap else None,
        pe_ratio=float(pe) if pe else None,
        pe_vs_median_ratio=pe_vs_median,
        fcf_yield_pct=fcf_yield,
        owner_earnings_latest=oe_latest,
        intrinsic_value_per_share=intrinsic_per_share,
        margin_of_safety_pct=mos,
        shareholder_yield_pct=shareholder_yield,
        currency_mismatch=currency_mismatch,
    )
