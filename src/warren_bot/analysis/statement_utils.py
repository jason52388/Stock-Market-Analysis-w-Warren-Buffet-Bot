"""Resilient lookups against yfinance statement DataFrames.

yfinance returns each statement as a DataFrame with line items on rows and
fiscal-year-end dates on columns (most recent first). Row names occasionally
shift between yfinance versions, so we try a list of candidates.
"""
from __future__ import annotations

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
