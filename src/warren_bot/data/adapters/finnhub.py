"""Finnhub adapter — real-time price / market-cap / shares validator (free tier).

Finnhub's free tier exposes a real-time ``quote`` endpoint and a ``stock/profile2``
company profile, but NOT financial statements (income/balance/cashflow are premium).
This adapter therefore contributes only a normalized ``quote`` dict and never
populates statements — it exists to cross-check price, market cap and share count.

Unit handling: ``profile2`` reports ``marketCapitalization`` in MILLIONS of USD and
``shareOutstanding`` in MILLIONS of shares. Both are multiplied by 1e6 here so the
``quote`` values are absolute (dollars / shares), matching the adapter contract.
"""
from __future__ import annotations

import logging

from ..cache import Cache
from ._http import RateLimiter, get_json
from .base import SourceAdapter, SourceResult

log = logging.getLogger(__name__)

_MILLION = 1e6


class FinnhubAdapter(SourceAdapter):
    name = "finnhub"

    def __init__(self, cache: Cache, *, api_key: str,
                 requests_per_sec: float = 1.0,
                 quote_ttl_seconds: int = 24 * 3600,
                 base_url: str = "https://finnhub.io/api/v1",
                 enabled: bool = True):
        super().__init__(enabled=enabled and bool(api_key))
        self.cache = cache
        self.api_key = api_key
        self.quote_ttl = quote_ttl_seconds
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(requests_per_sec)

    # --- endpoint helpers ----------------------------------------------------
    def _quote(self, ticker: str) -> dict | None:
        cached = self.cache.get("finnhub_quote", ticker, ttl_override=self.quote_ttl)
        if cached is not None:
            return cached
        data = get_json(f"{self.base_url}/quote",
                        params={"symbol": ticker, "token": self.api_key},
                        limiter=self.limiter)
        if isinstance(data, dict):
            self.cache.set("finnhub_quote", ticker, data)
            return data
        return None

    def _profile(self, ticker: str) -> dict | None:
        cached = self.cache.get("finnhub_profile", ticker, ttl_override=self.quote_ttl)
        if cached is not None:
            return cached
        data = get_json(f"{self.base_url}/stock/profile2",
                        params={"symbol": ticker, "token": self.api_key},
                        limiter=self.limiter)
        if isinstance(data, dict):
            self.cache.set("finnhub_profile", ticker, data)
            return data
        return None

    # --- fetch ---------------------------------------------------------------
    def fetch(self, ticker: str) -> SourceResult:
        if not self.enabled:
            return SourceResult(self.name, error="finnhub disabled")

        quote: dict[str, float] = {}
        info: dict = {}

        q = self._quote(ticker)
        if isinstance(q, dict):
            price = _num(q.get("c"))
            # c == 0 means no data for the symbol on the free tier.
            if price is not None and price != 0:
                quote["price"] = price

        prof = self._profile(ticker)
        if isinstance(prof, dict):
            mcap = _num(prof.get("marketCapitalization"))
            if mcap is not None and mcap != 0:
                quote["market_cap"] = mcap * _MILLION
            shares = _num(prof.get("shareOutstanding"))
            if shares is not None and shares != 0:
                quote["shares_outstanding"] = shares * _MILLION
            name = prof.get("name")
            if name:
                info["longName"] = name

        result = SourceResult(self.name, quote=quote, info=info)
        # Statements are never produced by this adapter.
        if not quote:
            result.error = "no quote or profile data"
        return result


def _num(value) -> float | None:
    """Coerce to float, returning None for missing / non-numeric values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
