"""yfinance wrapper with retry, batching, and a cache layer.

Returns a normalized `TickerSnapshot` so analytics code never touches yfinance directly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from .cache import Cache

log = logging.getLogger(__name__)


@dataclass
class TickerSnapshot:
    ticker: str
    info: dict[str, Any] = field(default_factory=dict)
    income: pd.DataFrame | None = None       # annual income statements
    balance: pd.DataFrame | None = None      # annual balance sheets
    cashflow: pd.DataFrame | None = None     # annual cashflows
    price_history: pd.DataFrame | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.income is not None and not self.income.empty


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def _fetch_ticker(ticker: str, min_market_cap: float = 0) -> TickerSnapshot:
    yft = yf.Ticker(ticker)
    snap = TickerSnapshot(ticker=ticker)
    try:
        snap.info = yft.info or {}
    except Exception as e:
        log.debug("info fetch failed for %s: %s", ticker, e)
        snap.info = {}

    # Cheap pre-filter: skip the expensive statement pulls for micro-caps.
    mcap = snap.info.get("marketCap") if snap.info else None
    if min_market_cap and (mcap is None or mcap < min_market_cap):
        snap.error = f"below min market cap (mcap={mcap})"
        return snap

    try:
        snap.income = yft.get_income_stmt(freq="yearly")
    except Exception as e:
        log.debug("income fetch failed for %s: %s", ticker, e)
    try:
        snap.balance = yft.get_balance_sheet(freq="yearly")
    except Exception as e:
        log.debug("balance fetch failed for %s: %s", ticker, e)
    try:
        snap.cashflow = yft.get_cashflow(freq="yearly")
    except Exception as e:
        log.debug("cashflow fetch failed for %s: %s", ticker, e)
    try:
        snap.price_history = yft.history(period="10y", interval="1mo", auto_adjust=False)
    except Exception as e:
        log.debug("history fetch failed for %s: %s", ticker, e)

    if snap.income is None or snap.income.empty:
        snap.error = "no financials returned"
    return snap


class Fetcher:
    def __init__(self, cache: Cache, batch_size: int = 25, batch_sleep_sec: float = 2.0,
                 min_market_cap: float = 0):
        self.cache = cache
        self.batch_size = batch_size
        self.batch_sleep_sec = batch_sleep_sec
        self.min_market_cap = min_market_cap

    def get(self, ticker: str, *, force_refresh: bool = False) -> TickerSnapshot:
        if not force_refresh:
            cached = self.cache.get("snapshot", ticker)
            if cached is not None:
                return cached
        try:
            snap = _fetch_ticker(ticker, min_market_cap=self.min_market_cap)
        except Exception as e:
            log.warning("fetch failed for %s: %s", ticker, e)
            snap = TickerSnapshot(ticker=ticker, error=str(e))
        # Cache even partial/failed fetches briefly so we don't hammer on retries.
        self.cache.set("snapshot", ticker, snap)
        return snap

    def get_many(self, tickers: list[str], *, force_refresh: bool = False) -> list[TickerSnapshot]:
        out: list[TickerSnapshot] = []
        for i, t in enumerate(tickers, 1):
            out.append(self.get(t, force_refresh=force_refresh))
            if i % self.batch_size == 0 and i < len(tickers):
                log.info("Fetched %d/%d, sleeping %.1fs", i, len(tickers), self.batch_sleep_sec)
                time.sleep(self.batch_sleep_sec)
        return out
