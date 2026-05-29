"""Tests for dashboard-only curation helpers."""
from __future__ import annotations

from types import SimpleNamespace

from warren_bot.dashboard.render import (
    _exchange_label,
    _format_market_cap,
    _listing_badges,
    build_ai_disruption_data,
)


def _pick(ticker: str, summary: str, *, sector: str = "Technology"):
    score = SimpleNamespace(
        ticker=ticker,
        name=f"{ticker} Co",
        sector=sector,
        total=70.0,
        error=None,
        valuation=SimpleNamespace(price=100.0, margin_of_safety_pct=None),
    )
    thesis = SimpleNamespace(summary=summary)
    return SimpleNamespace(score=score, thesis=thesis, snap_info={"longBusinessSummary": summary})


def _metadata_pick(ticker: str, info: dict):
    score = SimpleNamespace(ticker=ticker)
    return SimpleNamespace(score=score, snap_info=info)


class TestTickerMetadata:
    def test_formats_market_cap_compactly(self):
        assert _format_market_cap(3_210_000_000_000) == "$3.21T"
        assert _format_market_cap(245_600_000_000) == "$245.6B"
        assert _format_market_cap(850_000_000) == "$850M"

    def test_exchange_label_uses_friendly_mapping(self):
        pick = _metadata_pick("AAPL", {"exchange": "NMS"})
        assert _exchange_label(pick) == "Nasdaq"

    def test_listing_badges_include_sp500_and_watchlist(self):
        pick = _metadata_pick("AAPL", {})
        badges = _listing_badges(pick)
        assert "S&P 500" in badges
        assert "Watchlist" in badges


class TestAiDisruptionData:
    def test_requires_explicit_ai_context_not_just_tech_sector(self):
        picks = [
            _pick("GENERIC", "Enterprise software with durable margins."),
            _pick("AICLOUD", "Cloud infrastructure platform for artificial intelligence workloads."),
        ]

        data = build_ai_disruption_data(picks)

        assert [row["ticker"] for row in data["positive"]] == ["AICLOUD"]
        assert data["negative"] == []

    def test_negative_requires_ai_context_and_exposure(self):
        picks = [
            _pick("STAFF", "Professional staffing and recruiting services.", sector="Industrials"),
            _pick("AISTAFF", "AI automation for staffing, recruiting, and back office workflows.",
                  sector="Industrials"),
        ]

        data = build_ai_disruption_data(picks)

        assert data["positive"] == []
        assert [row["ticker"] for row in data["negative"]] == ["AISTAFF"]
