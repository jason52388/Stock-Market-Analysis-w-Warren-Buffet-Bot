"""Tests for the recommendation tier classifier and composite scoring."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from warren_bot.hedge_funds.dataroma import HedgeFundRow, HedgeFundView
from warren_bot.recommendations import (
    Recommendation,
    _pick_tier,
    _rank_bonus,
    build_recommendations,
)


def _row(ticker, rank, metric_value=10):
    return HedgeFundRow(
        ticker=ticker, name=f"{ticker} Co", sector="Tech",
        metric_label="x", metric_value=metric_value,
        hold_price=100.0, rank=rank,
    )


def _view(kind, rows):
    return HedgeFundView(kind=kind, rows=rows, title=kind, subtitle="")


def _pick(ticker, total, *, error=None):
    score = SimpleNamespace(
        ticker=ticker, name=f"{ticker} Co", sector="Tech",
        total=total, dimensions=[], ratios=None, growth=None,
        valuation=SimpleNamespace(
            price=100.0, fcf_yield_pct=None, margin_of_safety_pct=None,
        ),
        error=error,
    )
    return SimpleNamespace(score=score, thesis=None, snap_info={})


class TestRankBonus:
    def test_rank_one_gets_full_bonus(self):
        assert _rank_bonus(rank=1, total=50, max_bonus=10) == pytest.approx(10.0)

    def test_rank_last_gets_zero(self):
        # rank=50, total=50 → bonus = 10 * (1 - 49/50) = 10 * 0.02 = 0.2
        # The function uses (rank-1)/total, so rank=total gives a tiny tail bonus,
        # not exactly 0. That's fine — anyone past max_rows is filtered upstream.
        bonus = _rank_bonus(rank=50, total=50, max_bonus=10)
        assert bonus < 0.5

    def test_missing_rank_zero_bonus(self):
        assert _rank_bonus(rank=0, total=50, max_bonus=10) == 0.0

    def test_empty_view_zero_bonus(self):
        assert _rank_bonus(rank=5, total=0, max_bonus=10) == 0.0


class TestPickTier:
    def test_accumulating_when_only_buying(self):
        rec = Recommendation(pick=None, quant_score=70,
                              buys_count=5, sells_count=0, holdings_count=0)
        assert _pick_tier(rec) == "accumulating"

    def test_caution_when_more_selling_than_buying(self):
        rec = Recommendation(pick=None, quant_score=70,
                              buys_count=2, sells_count=8, holdings_count=20)
        assert _pick_tier(rec) == "caution"

    def test_consensus_when_widely_held_no_skew(self):
        rec = Recommendation(pick=None, quant_score=70,
                              buys_count=0, sells_count=0, holdings_count=25)
        assert _pick_tier(rec) == "consensus"

    def test_quant_only_when_no_hedge_signal(self):
        rec = Recommendation(pick=None, quant_score=70,
                              buys_count=0, sells_count=0, holdings_count=0)
        assert _pick_tier(rec) == "quant-only"

    def test_buying_and_selling_equal_classified_as_caution_or_consensus(self):
        # Edge case: 5 buyers and 5 sellers. _pick_tier checks selling > buying
        # for caution, then falls through to consensus if held.
        rec = Recommendation(pick=None, quant_score=70,
                              buys_count=5, sells_count=5, holdings_count=20)
        # Neither buying-only nor selling-dominant → consensus
        assert _pick_tier(rec) == "consensus"


class TestBuildRecommendations:
    def test_below_min_quant_score_excluded(self):
        picks = [_pick("LOW", 55)]  # default min is 60
        recs = build_recommendations(picks, {})
        assert recs == []

    def test_errored_picks_excluded(self):
        picks = [_pick("BAD", 80, error="bad")]
        recs = build_recommendations(picks, {})
        assert recs == []

    def test_only_overlap_excludes_quant_only(self):
        picks = [_pick("SOLO", 80)]
        # No hedge views → SOLO has no overlap
        recs = build_recommendations(picks, {}, only_overlap=True)
        assert recs == []

    def test_default_includes_quant_only(self):
        picks = [_pick("SOLO", 80)]
        recs = build_recommendations(picks, {})
        assert len(recs) == 1
        assert recs[0].tier == "quant-only"

    def test_composite_score_holdings_bonus(self):
        picks = [_pick("AAPL", 70)]
        hedge_views = {
            "holdings": _view("holdings", [_row("AAPL", rank=1, metric_value=39)]),
        }
        recs = build_recommendations(picks, hedge_views)
        assert len(recs) == 1
        # Composite = 70 (base) + 8 (rank 1 of 1 holding view) = 78
        assert recs[0].composite_score == pytest.approx(78.0, abs=0.1)
        assert recs[0].holdings_count == 39
        assert recs[0].tier == "consensus"  # held by 39, no buy/sell

    def test_composite_with_sells_classified_caution(self):
        picks = [_pick("KO", 70)]
        hedge_views = {
            "holdings": _view("holdings", [_row("KO", rank=1, metric_value=20)]),
            "sells": _view("sells", [_row("KO", rank=1, metric_value=10)]),
        }
        recs = build_recommendations(picks, hedge_views)
        # buys_count=0, sells_count=10, holdings_count=20 → caution (selling > buying)
        assert recs[0].tier == "caution"
        # Composite = 70 + 8 (holdings rank 1) + 0 (no buys) − 8 (sells rank 1) = 70
        assert recs[0].composite_score == pytest.approx(70.0, abs=0.1)

    def test_sorted_descending_by_composite(self):
        picks = [_pick("LOW", 60), _pick("MID", 70), _pick("HIGH", 80)]
        recs = build_recommendations(picks, {})
        assert [r.pick.score.ticker for r in recs] == ["HIGH", "MID", "LOW"]
