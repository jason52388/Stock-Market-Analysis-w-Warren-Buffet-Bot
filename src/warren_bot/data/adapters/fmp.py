"""Financial Modeling Prep (FMP) adapter — annual fundamentals + a market quote.

Pulls the three annual statements (income / balance sheet / cash flow) plus the
company ``profile`` for price and market cap, then normalizes everything into the
canonical shape declared in :mod:`warren_bot.data.schema`. FMP keys each yearly
object by a ``date`` field (fiscal-period-end) and exposes the financial line
items under the field names recorded in each ``Metric.fmp``.

FMP requires an ``apikey`` query parameter; without one the adapter is disabled.
"""
from __future__ import annotations

import logging

import pandas as pd

from .. import schema
from ..cache import Cache
from ..schema import BALANCE, CASHFLOW, INCOME, METRICS
from ._http import RateLimiter, get_json
from .base import SourceAdapter, SourceResult

log = logging.getLogger(__name__)


class FmpAdapter(SourceAdapter):
    name = "fmp"

    def __init__(self, cache: Cache, *, api_key: str,
                 requests_per_sec: float = 4.0,
                 statement_ttl_seconds: int = 30 * 24 * 3600,
                 quote_ttl_seconds: int = 24 * 3600,
                 base_url: str = "https://financialmodelingprep.com/api/v3",
                 enabled: bool = True):
        super().__init__(enabled=enabled and bool(api_key))
        self.cache = cache
        self.api_key = api_key
        self.statement_ttl = statement_ttl_seconds
        self.quote_ttl = quote_ttl_seconds
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(requests_per_sec)

    # --- raw endpoint helpers -----------------------------------------------
    def _statement_rows(self, endpoint: str, namespace: str, ticker: str) -> list:
        """Fetch (cached) a yearly statement array from one FMP endpoint."""
        cached = self.cache.get(namespace, ticker, ttl_override=self.statement_ttl)
        if cached is not None:
            return cached if isinstance(cached, list) else []
        url = f"{self.base_url}/{endpoint}/{ticker}"
        data = get_json(url, params={"period": "annual", "limit": 12,
                                     "apikey": self.api_key},
                        limiter=self.limiter)
        rows = data if isinstance(data, list) else []
        if rows:
            self.cache.set(namespace, ticker, rows)
        return rows

    def _profile(self, ticker: str) -> dict:
        cached = self.cache.get("fmp_profile", ticker, ttl_override=self.quote_ttl)
        if cached is not None:
            return cached if isinstance(cached, dict) else {}
        url = f"{self.base_url}/profile/{ticker}"
        data = get_json(url, params={"apikey": self.api_key}, limiter=self.limiter)
        row: dict = {}
        if isinstance(data, list) and data and isinstance(data[0], dict):
            row = data[0]
        elif isinstance(data, dict):
            row = data
        if row:
            self.cache.set("fmp_profile", ticker, row)
        return row

    # --- fetch ---------------------------------------------------------------
    def fetch(self, ticker: str) -> SourceResult:
        if not self.enabled:
            return SourceResult(self.name, error="fmp disabled")

        income_rows = self._statement_rows("income-statement", "fmp_income", ticker)
        balance_rows = self._statement_rows("balance-sheet-statement", "fmp_balance", ticker)
        cashflow_rows = self._statement_rows("cash-flow-statement", "fmp_cashflow", ticker)
        profile = self._profile(ticker)

        income = _statement(income_rows, INCOME)
        balance = _statement(balance_rows, BALANCE)
        cashflow = _statement(cashflow_rows, CASHFLOW)

        quote: dict[str, float] = {}
        price = _num(profile.get("price"))
        if price is not None:
            quote["price"] = price
        mkt_cap = _num(profile.get("mktCap"))
        if mkt_cap is not None:
            quote["market_cap"] = mkt_cap

        shares = _latest_shares(income_rows)
        if shares is None:
            shares = _num(profile.get("sharesOutstanding"))
        if shares is not None:
            quote["shares_outstanding"] = shares

        result = SourceResult(self.name, income=income, balance=balance,
                              cashflow=cashflow, quote=quote)
        if not result.has_statements and not quote:
            result.error = "no annual statements or quote parsed"
        return result


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _series(rows: list, fields: list[str], *, negate: bool) -> pd.Series | None:
    """Build a date-indexed series for one metric from FMP yearly objects."""
    data: dict[pd.Timestamp, float] = {}
    for obj in rows:
        if not isinstance(obj, dict):
            continue
        date = obj.get("date")
        if not date:
            continue
        val = None
        for f in fields:
            if f in obj and obj[f] is not None:
                val = _num(obj[f])
                if val is not None:
                    break
        if val is None:
            continue
        try:
            ts = pd.Timestamp(date)
        except (ValueError, TypeError):
            continue
        data[ts] = -val if negate else val
    if not data:
        return None
    return pd.Series(data).sort_index()


def _statement(rows: list, which: str) -> pd.DataFrame | None:
    out: dict[str, pd.Series] = {}
    for m in METRICS:
        if m.statement != which or not m.fmp:
            continue
        s = _series(rows, m.fmp, negate=m.negate_fmp)
        if s is not None:
            out[m.label] = s
    return schema.build_statement(out)


def _latest_shares(income_rows: list) -> float | None:
    """Most-recent share count from the income statement (FMP returns most-recent
    first, but we sort defensively)."""
    s = _series(income_rows, schema.BY_LABEL["Ordinary Shares Number"].fmp, negate=False)
    if s is None or not len(s):
        return None
    return float(s.sort_index().iloc[-1])
