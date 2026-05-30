"""Tests for the Finnhub adapter — price/market-cap/shares validator only.

Patches ``warren_bot.data.adapters.finnhub.get_json`` so no real HTTP happens,
and uses a real on-disk Cache at tmp_path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from warren_bot.data.cache import Cache
from warren_bot.data.adapters.finnhub import FinnhubAdapter

_QUOTE = {"c": 187.5, "h": 190.0, "l": 185.0, "o": 186.0, "pc": 186.4, "t": 1700000000}
_PROFILE = {
    "marketCapitalization": 2400000,   # millions USD
    "shareOutstanding": 15700,         # millions of shares
    "name": "Apple Inc",
    "ticker": "AAPL",
}


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    c = Cache(tmp_path / "cache.sqlite", ttl_seconds=3600)
    yield c
    c.close()


def _fake_get_json(quote=_QUOTE, profile=_PROFILE):
    def _inner(url, *, params=None, headers=None, limiter=None, **kwargs):
        if "stock/profile2" in url:
            return profile
        if "/quote" in url:
            return quote
        raise AssertionError(f"unexpected url: {url}")
    return _inner


def test_quote_price_from_c(cache, monkeypatch):
    monkeypatch.setattr("warren_bot.data.adapters.finnhub.get_json", _fake_get_json())
    adapter = FinnhubAdapter(cache, api_key="KEY")
    result = adapter.fetch("AAPL")
    assert result.quote["price"] == _QUOTE["c"]


def test_millions_to_absolute_conversion(cache, monkeypatch):
    monkeypatch.setattr("warren_bot.data.adapters.finnhub.get_json", _fake_get_json())
    adapter = FinnhubAdapter(cache, api_key="KEY")
    result = adapter.fetch("AAPL")
    assert result.quote["market_cap"] == _PROFILE["marketCapitalization"] * 1e6
    assert result.quote["shares_outstanding"] == _PROFILE["shareOutstanding"] * 1e6


def test_no_statements_ever(cache, monkeypatch):
    monkeypatch.setattr("warren_bot.data.adapters.finnhub.get_json", _fake_get_json())
    adapter = FinnhubAdapter(cache, api_key="KEY")
    result = adapter.fetch("AAPL")
    assert result.income is None
    assert result.balance is None
    assert result.cashflow is None
    assert result.has_statements is False


def test_disabled_makes_no_http_calls(cache, monkeypatch):
    calls = []

    def _spy(url, **kwargs):
        calls.append(url)
        return {}

    monkeypatch.setattr("warren_bot.data.adapters.finnhub.get_json", _spy)
    adapter = FinnhubAdapter(cache, api_key="")
    assert adapter.enabled is False
    result = adapter.fetch("AAPL")
    assert result.error is not None
    assert calls == []


def test_zero_current_price_not_set(cache, monkeypatch):
    quote = dict(_QUOTE, c=0)
    monkeypatch.setattr("warren_bot.data.adapters.finnhub.get_json",
                        _fake_get_json(quote=quote))
    adapter = FinnhubAdapter(cache, api_key="KEY")
    result = adapter.fetch("AAPL")
    assert "price" not in result.quote
    # profile still provides market cap / shares
    assert result.quote["market_cap"] == _PROFILE["marketCapitalization"] * 1e6
