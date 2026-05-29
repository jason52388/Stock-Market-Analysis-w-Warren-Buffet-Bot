"""yfinance wrapper with retry, batching, and a cache layer.

Returns a normalized `TickerSnapshot` so analytics code never touches yfinance directly.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from .cache import Cache

log = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe minimum-interval gate shared across worker threads.

    The screener fans tickers out across a thread pool, and each ticker makes
    several Yahoo requests. Without a global limiter those requests arrive in
    bursts and Yahoo answers with 429s or — worse — silent empty frames. This
    gate serializes the *moment of release* so the aggregate request rate across
    ALL threads stays under `requests_per_sec`, no matter how many workers run.
    """

    def __init__(self, requests_per_sec: float):
        self.min_interval = (1.0 / requests_per_sec) if requests_per_sec and requests_per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        # Hold the lock across the sleep so threads release one-per-interval.
        with self._lock:
            now = time.monotonic()
            wait_for = self._next_allowed - now
            if wait_for > 0:
                time.sleep(wait_for)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self.min_interval


# A process-wide default so module-level helpers (and tests) always have a gate
# even before a Fetcher configures one. Fetcher.__init__ replaces it with the
# settings-driven instance.
_LIMITER = RateLimiter(0.0)

_YF_CONFIGURED = False


def _configure_yfinance(retries: int) -> None:
    """Bump yfinance's own per-request retry count (best effort; older versions
    lack set_config). This stacks under our blank-retry: yfinance retries hard
    HTTP failures, we retry silent empties."""
    global _YF_CONFIGURED
    if _YF_CONFIGURED or not retries:
        return
    _YF_CONFIGURED = True
    try:
        # Newer yfinance exposes `yf.config.network.retries`; older versions use
        # `yf.set_config(retries=...)` (now deprecated). Prefer the new path to
        # avoid the deprecation warning, fall back for the pinned floor.
        cfg = getattr(yf, "config", None)
        net = getattr(cfg, "network", None) if cfg is not None else None
        if net is not None and hasattr(net, "retries"):
            net.retries = int(retries)
        elif hasattr(yf, "set_config"):
            yf.set_config(retries=int(retries))
    except Exception as e:  # pragma: no cover - version dependent
        log.debug("configuring yfinance retries=%s failed: %s", retries, e)


def _pull(getter: Callable[[], "pd.DataFrame | None"], limiter: RateLimiter,
          *, attempts: int, backoff: float) -> "pd.DataFrame | None":
    """Fetch a statement through the rate limiter, re-pulling when it comes back
    empty/None — the signature of a silently-throttled response that yfinance
    does NOT raise on (so tenacity never sees it). Returns the last result."""
    last: "pd.DataFrame | None" = None
    for i in range(max(1, attempts)):
        limiter.wait()
        try:
            df = getter()
        except Exception as e:
            log.debug("statement pull failed: %s", e)
            df = None
        if df is not None and not getattr(df, "empty", True):
            return df
        last = df
        if i < attempts - 1 and backoff:
            time.sleep(backoff * (i + 1))
    return last


# Statements a Buffett screen needs before a ticker is scoreable. yfinance pulls
# each of these from Yahoo in a SEPARATE request, and Yahoo's throttling can
# blank any one of them independently — so "missing cash flow" almost always
# means a throttled request, not a company without a cash-flow statement. We
# require the full set and treat a partial pull as a (transient) failure rather
# than scoring around the hole.
REQUIRED_STATEMENTS = ("income", "balance", "cashflow", "price_history")


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
    statements_asof: float | None = None     # epoch secs when statements were last pulled

    def missing_statements(self) -> list[str]:
        """Which of the REQUIRED_STATEMENTS came back empty/absent."""
        missing = []
        for name in REQUIRED_STATEMENTS:
            df = getattr(self, name)
            if df is None or getattr(df, "empty", True):
                missing.append(name)
        return missing

    @property
    def ok(self) -> bool:
        # Scoreable as soon as we have income — the scorer is a pure function and
        # downstream unit tests / the single-ticker `screen` command rely on it
        # producing a partial breakdown. The full-completeness gate is enforced
        # in the fetch path (it sets `error`), so pipeline runs never score an
        # incomplete snapshot: an errored snapshot makes this False too.
        return self.error is None and self.income is not None and not self.income.empty


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def _fetch_ticker(ticker: str, min_market_cap: float = 0, *,
                  limiter: RateLimiter | None = None,
                  blank_retries: int = 0, blank_retry_backoff_sec: float = 1.5) -> TickerSnapshot:
    limiter = limiter or _LIMITER
    attempts = blank_retries + 1
    yft = yf.Ticker(ticker)
    snap = TickerSnapshot(ticker=ticker)
    limiter.wait()
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
            limiter.wait()
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

    # Each statement is rate-limited and re-pulled if it returns empty (a
    # silently-throttled response). This is where most "blank data" was coming
    # from — yfinance returns an empty frame without raising, so the only way to
    # recover within the run is to space the calls out and try the blank again.
    snap.income = _pull(lambda: yft.get_income_stmt(freq="yearly"), limiter,
                        attempts=attempts, backoff=blank_retry_backoff_sec)
    snap.balance = _pull(lambda: yft.get_balance_sheet(freq="yearly"), limiter,
                         attempts=attempts, backoff=blank_retry_backoff_sec)
    snap.cashflow = _pull(lambda: yft.get_cashflow(freq="yearly"), limiter,
                          attempts=attempts, backoff=blank_retry_backoff_sec)
    snap.price_history = _pull(
        lambda: yft.history(period="10y", interval="1mo", auto_adjust=False),
        limiter, attempts=attempts, backoff=blank_retry_backoff_sec)

    now = time.time()
    snap.price_asof = now
    snap.statements_asof = now   # stamp the statement pull so cheap quote
    # refreshes (which re-cache the snapshot) don't reset the statement clock.
    missing = snap.missing_statements()
    if missing:
        # Don't score around a hole. A partial pull (e.g. income + balance but a
        # throttled cash flow) is reported as incomplete and re-fetched next run
        # rather than cached for a week and silently scored on the data we have.
        snap.error = "incomplete data: missing " + ", ".join(missing)
    return snap


def _refresh_quote(snap: TickerSnapshot, limiter: RateLimiter | None = None) -> bool:
    """Best-effort refresh of just the fast-moving fields (price + market cap)
    on an otherwise-valid cached snapshot, so week-old statements don't drag a
    week-old *price* into the valuation. Returns True if anything was updated."""
    (limiter or _LIMITER).wait()
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
    # Price/market cap go stale fast — refresh just the quote on a cache hit
    # older than this (default 1 day) without re-pulling statements.
    _PRICE_TTL_SECONDS = 24 * 60 * 60
    # Statements only change on quarterly earnings, so they're cached far longer
    # than the price (default 30 days). This is what keeps the WEEKLY run cheap:
    # statements are reused (a ~1-request quote refresh per name) instead of a
    # full multi-request re-pull, while a longer-dormant cache still refreshes.
    _STATEMENT_TTL_SECONDS = 30 * 24 * 60 * 60

    def __init__(self, cache: Cache, batch_size: int = 25, batch_sleep_sec: float = 2.0,
                 min_market_cap: float = 0, price_ttl_seconds: int | None = None,
                 *, statement_ttl_seconds: int | None = None,
                 requests_per_sec: float = 0.0, blank_retries: int = 0,
                 blank_retry_backoff_sec: float = 1.5, yf_internal_retries: int = 0):
        self.cache = cache
        self.batch_size = batch_size
        self.batch_sleep_sec = batch_sleep_sec
        self.min_market_cap = min_market_cap
        self.price_ttl_seconds = (price_ttl_seconds if price_ttl_seconds is not None
                                  else self._PRICE_TTL_SECONDS)
        self.statement_ttl_seconds = (statement_ttl_seconds if statement_ttl_seconds is not None
                                      else self._STATEMENT_TTL_SECONDS)
        # Throttle controls. The limiter is shared across all worker threads, so
        # the aggregate Yahoo request rate stays bounded regardless of pool size.
        self.limiter = RateLimiter(requests_per_sec)
        self.blank_retries = blank_retries
        self.blank_retry_backoff_sec = blank_retry_backoff_sec
        global _LIMITER
        _LIMITER = self.limiter  # so _refresh_quote / bare helpers share the gate
        _configure_yfinance(yf_internal_retries)

    def _price_is_stale(self, snap: TickerSnapshot) -> bool:
        if snap.price_asof is None:
            return True
        return (time.time() - snap.price_asof) > self.price_ttl_seconds

    def _statements_are_stale(self, snap: TickerSnapshot) -> bool:
        # Judged on `statements_asof` (stamped when statements were pulled), NOT
        # the cache row's age — a quote refresh re-caches the row and would
        # otherwise reset the statement clock and keep statements alive forever.
        asof = getattr(snap, "statements_asof", None)
        if asof is None:
            return True
        return (time.time() - asof) > self.statement_ttl_seconds

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
        # Incomplete statements are almost always a throttled per-statement pull,
        # not a company that genuinely lacks the filing — re-fetch next run so a
        # transient blank doesn't exclude a valid name for the full cache window.
        if snap.error.startswith("incomplete data"):
            return True
        # Anything that came back with a populated info dict is stable.
        return False

    def get(self, ticker: str, *, force_refresh: bool = False) -> TickerSnapshot:
        if not force_refresh:
            # Read with the (long) statement TTL so quarterly-stable statements
            # are reused across weekly runs instead of re-pulled every time.
            cached = self.cache.get("snapshot", ticker,
                                    ttl_override=self.statement_ttl_seconds)
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
                    if cached.ok and self._statements_are_stale(cached):
                        cached = None  # statements aged out -> full re-pull below
                    else:
                        # Statements still fresh. Refresh just the price if it's
                        # gone stale so valuation doesn't run on an old quote —
                        # a single request vs a full multi-request statement pull.
                        if cached.ok and self._price_is_stale(cached):
                            if _refresh_quote(cached, self.limiter):
                                self.cache.set("snapshot", ticker, cached)
                        return cached
        try:
            snap = _fetch_ticker(
                ticker, min_market_cap=self.min_market_cap,
                limiter=self.limiter, blank_retries=self.blank_retries,
                blank_retry_backoff_sec=self.blank_retry_backoff_sec,
            )
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
