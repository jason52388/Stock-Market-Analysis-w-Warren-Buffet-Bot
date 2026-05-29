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
    price_asof: float | None = None          # epoch secs when price/mcap last refreshed

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
    if min_market_cap and mcap is None:
        # Yahoo's regular info endpoint often drops marketCap during throttling
        # or partial responses. Try fast_info before deciding the ticker is
        # below the cap threshold, otherwise one partial response can hide a
        # perfectly valid large-cap stock for the cache TTL.
        try:
            fi = yft.fast_info
            fast_mcap = (
                fi.get("market_cap") if hasattr(fi, "get")
                else getattr(fi, "market_cap", None)
            )
            if fast_mcap:
                mcap = float(fast_mcap)
                snap.info["marketCap"] = mcap
        except Exception as e:
            log.debug("fast_info market cap fetch failed for %s: %s", ticker, e)
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

    snap.price_asof = time.time()
    if snap.income is None or snap.income.empty:
        snap.error = "no financials returned"
    return snap


def _refresh_quote(snap: TickerSnapshot) -> bool:
    """Best-effort refresh of just the fast-moving fields (price + market cap)
    on an otherwise-valid cached snapshot, so week-old statements don't drag a
    week-old *price* into the valuation. Returns True if anything was updated."""
    try:
        fi = yf.Ticker(snap.ticker).fast_info
        last = fi.get("last_price") if hasattr(fi, "get") else getattr(fi, "last_price", None)
        mcap = fi.get("market_cap") if hasattr(fi, "get") else getattr(fi, "market_cap", None)
    except Exception as e:  # pragma: no cover - network dependent
        log.debug("quote refresh failed for %s: %s", snap.ticker, e)
        return False
    updated = False
    if last:
        snap.info["currentPrice"] = float(last)
        snap.info["regularMarketPrice"] = float(last)
        updated = True
    if mcap:
        snap.info["marketCap"] = float(mcap)
        updated = True
    if updated:
        snap.price_asof = time.time()
    return updated


class Fetcher:
    # Transient errors (network/throttle, empty info dict) cache for 1 hour, so
    # the next weekly run gets a fresh shot. Stable errors (legitimate sub-cap
    # tickers, delisted) cache for the normal TTL — no reason to refetch.
    _TRANSIENT_TTL_SECONDS = 60 * 60
    # Statements are cached for the full TTL, but price/market cap go stale fast.
    # Refresh just the quote on a cache hit older than this (default 1 day).
    _PRICE_TTL_SECONDS = 24 * 60 * 60

    def __init__(self, cache: Cache, batch_size: int = 25, batch_sleep_sec: float = 2.0,
                 min_market_cap: float = 0, price_ttl_seconds: int | None = None):
        self.cache = cache
        self.batch_size = batch_size
        self.batch_sleep_sec = batch_sleep_sec
        self.min_market_cap = min_market_cap
        self.price_ttl_seconds = (price_ttl_seconds if price_ttl_seconds is not None
                                  else self._PRICE_TTL_SECONDS)

    def _price_is_stale(self, snap: TickerSnapshot) -> bool:
        if snap.price_asof is None:
            return True
        return (time.time() - snap.price_asof) > self.price_ttl_seconds

    @staticmethod
    def _is_transient_error(snap: TickerSnapshot) -> bool:
        """A fetch is 'transient' when we got no info dict at all — usually
        yfinance throttling. Distinguish from 'stable' (info present but mcap
        below threshold, or financials unavailable for known-bad ticker)."""
        if snap.error is None:
            return False
        # Empty info → almost certainly a network/throttle blip; retry sooner.
        if not snap.info:
            return True
        # A missing market cap from an otherwise populated info dict is also a
        # partial Yahoo response. Treat it as transient so alphabetically later
        # large caps don't get suppressed for the full weekly cache window.
        if "below min market cap" in snap.error and "mcap=None" in snap.error:
            return True
        # Anything that came back with a populated info dict is stable.
        return False

    def get(self, ticker: str, *, force_refresh: bool = False) -> TickerSnapshot:
        if not force_refresh:
            cached = self.cache.get("snapshot", ticker)
            if cached is not None:
                # Honor the transient short TTL even on cached entries — a
                # transient failure cached a week ago should re-fetch this run.
                if cached.error and self._is_transient_error(cached):
                    cached_fresh = self.cache.get(
                        "snapshot", ticker, ttl_override=self._TRANSIENT_TTL_SECONDS
                    )
                    if cached_fresh is None:
                        cached = None  # fall through to re-fetch
                if cached is not None:
                    # Statements are still fresh, but refresh the price if it's
                    # gone stale so the valuation doesn't run on a week-old quote.
                    if cached.ok and self._price_is_stale(cached):
                        if _refresh_quote(cached):
                            self.cache.set("snapshot", ticker, cached)
                    return cached
        try:
            snap = _fetch_ticker(ticker, min_market_cap=self.min_market_cap)
        except Exception as e:
            log.warning("fetch failed for %s: %s", ticker, e)
            snap = TickerSnapshot(ticker=ticker, error=str(e))
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
