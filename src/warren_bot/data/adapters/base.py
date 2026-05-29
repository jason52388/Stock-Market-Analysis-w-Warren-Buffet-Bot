"""Adapter contract shared by every data source."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class SourceResult:
    """One source's contribution toward a ticker, in canonical shape.

    Statements are DataFrames indexed by canonical labels (see ``data.schema``)
    with fiscal-period-end dates on the columns. ``quote`` is the normalized
    market view every source can speak even when it has no statements:
    ``{"price": float, "market_cap": float, "shares_outstanding": float}``.
    ``info`` carries the yfinance-style descriptive dict (only yfinance populates
    it fully; other sources add the few keys they know). A non-None ``error``
    means the source had nothing usable for this ticker.
    """
    source: str
    info: dict[str, Any] = field(default_factory=dict)
    income: pd.DataFrame | None = None
    balance: pd.DataFrame | None = None
    cashflow: pd.DataFrame | None = None
    price_history: pd.DataFrame | None = None
    quote: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    @property
    def has_statements(self) -> bool:
        return any(df is not None and not df.empty
                   for df in (self.income, self.balance, self.cashflow))

    def statement(self, name: str) -> pd.DataFrame | None:
        return {"income": self.income, "balance": self.balance,
                "cashflow": self.cashflow, "price_history": self.price_history}.get(name)


class SourceAdapter:
    """Base class for data-source adapters.

    Subclasses set ``name`` and implement :meth:`fetch`. ``enabled`` lets the
    coordinator skip a source cleanly (e.g. an API key wasn't supplied) without
    special-casing it at the call site.
    """
    name: str = "base"

    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled

    def fetch(self, ticker: str) -> SourceResult:  # pragma: no cover - interface
        raise NotImplementedError
