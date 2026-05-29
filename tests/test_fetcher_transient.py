"""Regression tests for the new transient-vs-stable error caching behavior."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from warren_bot.data.fetcher import Fetcher, TickerSnapshot, _fetch_ticker  # noqa: F401


class TestIsTransientError:
    """Empty info dict = transient (yfinance throttle). Populated info = stable."""

    def test_transient_empty_info(self):
        snap = TickerSnapshot(ticker="AAPL", info={}, error="no info returned")
        assert Fetcher._is_transient_error(snap) is True

    def test_stable_with_info(self):
        # Has info but failed mcap pre-filter → stable, don't refetch every run
        snap = TickerSnapshot(
            ticker="TINY",
            info={"marketCap": 1_000_000, "shortName": "Tiny Corp"},
            error="below min market cap (mcap=1000000)",
        )
        assert Fetcher._is_transient_error(snap) is False

    def test_missing_market_cap_prefilter_is_transient(self):
        snap = TickerSnapshot(
            ticker="CAT",
            info={"shortName": "Caterpillar Inc."},
            error="below min market cap (mcap=None)",
        )
        assert Fetcher._is_transient_error(snap) is True

    def test_no_error_not_transient(self):
        snap = TickerSnapshot(ticker="AAPL", info={"x": 1}, error=None)
        assert Fetcher._is_transient_error(snap) is False


class TestMarketCapPrefilter:
    def test_fast_info_market_cap_rescues_partial_info(self):
        from types import SimpleNamespace

        ticker_obj = SimpleNamespace(
            info={"shortName": "Caterpillar Inc."},
            fast_info={"market_cap": 150_000_000_000},
            get_income_stmt=lambda freq: None,
            get_balance_sheet=lambda freq: None,
            get_cashflow=lambda freq: None,
            history=lambda **kwargs: None,
        )

        with patch("warren_bot.data.fetcher.yf.Ticker", return_value=ticker_obj):
            snap = _fetch_ticker("CAT", min_market_cap=300_000_000)

        assert snap.info["marketCap"] == 150_000_000_000
        assert snap.error == "no financials returned"


class TestTransientShortTTL:
    """Cached transient errors should re-fetch sooner than the default TTL."""

    def test_stable_error_not_refetched(self, tmp_cache):
        """A stable (info-populated) error stays cached for the full TTL."""
        fetcher = Fetcher(tmp_cache, min_market_cap=0)
        stable_snap = TickerSnapshot(
            ticker="TINY",
            info={"marketCap": 1_000_000},
            error="below min market cap",
        )
        tmp_cache.set("snapshot", "TINY", stable_snap)

        # Patch _fetch_ticker so we can detect whether the fetcher actually called it
        with patch("warren_bot.data.fetcher._fetch_ticker") as mock_fetch:
            result = fetcher.get("TINY")
            mock_fetch.assert_not_called()
        assert result.error == "below min market cap"

    def test_transient_error_within_short_ttl_returned(self, tmp_cache):
        """A transient error fetched <1h ago is returned from cache without refetch."""
        fetcher = Fetcher(tmp_cache, min_market_cap=0)
        transient_snap = TickerSnapshot(ticker="AAPL", info={}, error="throttle")
        tmp_cache.set("snapshot", "AAPL", transient_snap)

        with patch("warren_bot.data.fetcher._fetch_ticker") as mock_fetch:
            result = fetcher.get("AAPL")
            mock_fetch.assert_not_called()
        assert result.error == "throttle"

    def test_transient_error_past_short_ttl_refetched(self, tmp_path):
        """A transient error fetched longer than the transient TTL ago must refetch."""
        from warren_bot.data.cache import Cache

        cache = Cache(tmp_path / "f.sqlite", ttl_seconds=3600)
        fetcher = Fetcher(cache, min_market_cap=0)

        # Cache a transient error with a fake fetched_at in the past (older than
        # the 1-hour transient TTL).
        transient_snap = TickerSnapshot(ticker="AAPL", info={}, error="throttle")
        cache.set("snapshot", "AAPL", transient_snap)
        # Rewrite the fetched_at to 2h ago, which exceeds the 1h transient TTL.
        import time

        two_hours_ago = time.time() - 7200
        with cache._lock:
            cache._conn.execute(
                "UPDATE cache SET fetched_at = ? WHERE namespace = ? AND key = ?",
                (two_hours_ago, "snapshot", "AAPL"),
            )
            cache._conn.commit()

        fresh_snap = TickerSnapshot(ticker="AAPL", info={"marketCap": 3e12}, error=None)
        with patch("warren_bot.data.fetcher._fetch_ticker", return_value=fresh_snap) as mock_fetch:
            result = fetcher.get("AAPL")
            mock_fetch.assert_called_once()
        assert result.info["marketCap"] == 3e12
        cache.close()

    def test_force_refresh_bypasses_cache(self, tmp_cache):
        fetcher = Fetcher(tmp_cache, min_market_cap=0)
        cached = TickerSnapshot(ticker="AAPL", info={"x": 1})
        tmp_cache.set("snapshot", "AAPL", cached)

        fresh = TickerSnapshot(ticker="AAPL", info={"x": 999})
        with patch("warren_bot.data.fetcher._fetch_ticker", return_value=fresh) as mock_fetch:
            result = fetcher.get("AAPL", force_refresh=True)
            mock_fetch.assert_called_once()
        assert result.info["x"] == 999
