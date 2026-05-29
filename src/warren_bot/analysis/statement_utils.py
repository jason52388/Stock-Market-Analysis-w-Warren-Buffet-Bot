"""Resilient lookups against yfinance statement DataFrames.

yfinance returns each statement as a DataFrame with line items on rows and
fiscal-year-end dates on columns (most recent first). Row names occasionally
shift between yfinance versions, so we try a list of candidates.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd


def _normalize(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def row(df: pd.DataFrame | None, candidates: Iterable[str]) -> pd.Series | None:
    """Return the first matching row.

    Matches case-insensitively and ignores spaces/punctuation, so both
    'Total Revenue' and 'TotalRevenue' / 'totalrevenue' hit the same row.
    """
    if df is None or df.empty:
        return None
    norm = {_normalize(str(idx)): idx for idx in df.index}
    for c in candidates:
        key = _normalize(c)
        if key in norm:
            return df.loc[norm[key]]
    return None


def latest(series: pd.Series | None) -> float | None:
    if series is None or series.empty:
        return None
    val = series.dropna()
    if val.empty:
        return None
    # yfinance columns are dates; sort descending so [0] is most recent
    val = val.sort_index(ascending=False)
    v = val.iloc[0]
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def series_values(series: pd.Series | None, *, sort_oldest_first: bool = True) -> list[float]:
    if series is None or series.empty:
        return []
    s = series.dropna().sort_index(ascending=sort_oldest_first)
    out: list[float] = []
    for v in s.values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def cagr(values: list[float]) -> float | None:
    """CAGR from oldest -> newest. Returns None if undefined."""
    if len(values) < 2:
        return None
    start, end = values[0], values[-1]
    if start is None or end is None:
        return None
    if start <= 0 or end <= 0:
        # Sign change makes CAGR meaningless; fall back to None.
        return None
    n = len(values) - 1
    return (end / start) ** (1 / n) - 1


def safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def avg(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if not clean:
        return None
    return sum(clean) / len(clean)


def frame(**rows: "pd.Series | None") -> pd.DataFrame:
    """Align several statement rows on their (date) index into one DataFrame.

    This is the safe alternative to extracting each row to a list with
    ``series_values`` and then pairing the lists positionally — that approach
    silently misaligns fiscal years whenever two statements have a different
    number of periods, or a single value is NaN and gets dropped from only one
    series. ``pd.concat(..., axis=1)`` aligns on the shared date index instead,
    so ``row.net`` and ``row.equity`` always refer to the *same* fiscal year.

    Columns are named by the keyword. ``None`` rows are skipped. Rows with a
    duplicated index (rare yfinance quirk) keep the first occurrence. Result is
    sorted oldest-first.
    """
    data: dict[str, pd.Series] = {}
    for name, s in rows.items():
        if s is None or len(s) == 0:
            continue
        s = s[~s.index.duplicated(keep="first")]
        data[name] = pd.to_numeric(s, errors="coerce")
    if not data:
        return pd.DataFrame()
    df = pd.concat(data, axis=1, sort=True)  # union of indices, sorted oldest-first
    return df.sort_index()


def aligned(df: pd.DataFrame, *names: str, require: Iterable[str] | None = None) -> pd.DataFrame:
    """Return rows of ``df`` (oldest-first) where the ``require`` columns are all
    present and non-NaN. If ``require`` is None, every column in ``names`` (or all
    columns) must be present. Missing optional columns are filled with NaN so
    callers can read them with ``getattr(row, name, nan)``."""
    if df.empty:
        return df
    cols = list(names) or list(df.columns)
    need = list(require) if require is not None else list(cols)
    # Ensure every referenced column exists. A required column whose source row
    # was missing becomes all-NaN here, so the dropna below correctly yields an
    # empty frame (no data -> no metric) instead of a downstream AttributeError.
    for c in set(cols) | set(need):
        if c not in df.columns:
            df[c] = np.nan
    return df.dropna(subset=need) if need else df


def trend_growth(values: list[float | None]) -> float | None:
    """Robust annualized growth via log-linear least squares (oldest -> newest).

    Endpoint CAGR is hostage to its two boundary years; one outlier endpoint
    makes the rate meaningless. Fitting ``ln(y) = a + b*t`` across *all* points
    and taking ``exp(b) - 1`` uses the whole series and is far less sensitive to
    a single noisy year.

    Returns None if fewer than 2 usable (positive) points. With exactly 2 points
    it falls back to plain CAGR (a line through 2 points *is* the endpoint rate).
    """
    vals = [float(v) for v in values
            if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2 or any(v <= 0 for v in vals):
        return None
    if len(vals) == 2:
        return cagr(vals)
    n = len(vals)
    xs = list(range(n))
    ys = [math.log(v) for v in vals]
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return math.exp(b) - 1


def dividend_yield_pct(info: dict | None) -> float:
    """Return dividend yield as a percent (e.g. 2.5 for 2.5%), robustly.

    yfinance's ``dividendYield`` field has flip-flopped between a fraction
    (0.025) and a percent (2.5) across versions, and the magnitude alone can't
    disambiguate sub-1% yielders (0.5 could be 0.5% or 50%). When an absolute
    dividend rate is available we derive the yield from first principles
    (rate / price) which is version-independent; otherwise we fall back to the
    magnitude heuristic. Centralized here so every module normalizes identically.
    """
    if not info:
        return 0.0
    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    rate = info.get("trailingAnnualDividendRate") or info.get("dividendRate")
    if rate and price and price > 0:
        return float(rate) / float(price) * 100.0
    dy = info.get("dividendYield")
    if not dy:
        return 0.0
    dy = float(dy)
    # > 1 is already a percent; <= 1 is a fraction -> scale to percent.
    return dy if dy > 1 else dy * 100.0
