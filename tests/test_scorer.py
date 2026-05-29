"""Tests for the piecewise scoring function — the math at boundaries."""
from __future__ import annotations

import pytest

from warren_bot.analysis.scorer import _piecewise_score, _status


class TestPiecewiseHigherIsBetter:
    """Default direction: 0 → 0, target → 70, excellent → 100."""

    def test_at_target_scores_70(self):
        assert _piecewise_score(15.0, target=15, excellent=25) == pytest.approx(70.0)

    def test_at_excellent_scores_100(self):
        assert _piecewise_score(25.0, target=15, excellent=25) == 100.0

    def test_above_excellent_caps_at_100(self):
        assert _piecewise_score(100.0, target=15, excellent=25) == 100.0

    def test_at_zero_scores_zero(self):
        assert _piecewise_score(0.0, target=15, excellent=25) == 0.0

    def test_below_zero_scores_zero(self):
        assert _piecewise_score(-5.0, target=15, excellent=25) == 0.0

    def test_halfway_below_target_is_proportional(self):
        # value=7.5 is half of target=15 → should be half of 70 = 35
        assert _piecewise_score(7.5, target=15, excellent=25) == pytest.approx(35.0)

    def test_halfway_target_to_excellent_is_proportional(self):
        # value=20 is halfway from 15→25 → 70 + half of 30 = 85
        assert _piecewise_score(20.0, target=15, excellent=25) == pytest.approx(85.0)

    def test_none_value_scores_zero(self):
        assert _piecewise_score(None, target=15, excellent=25) == 0.0


class TestPiecewiseLowerIsBetter:
    """Mirrored: lower values are better. Target=0.5 (D/E), excellent=0.2."""

    def test_at_or_below_excellent_scores_100(self):
        assert _piecewise_score(0.2, target=0.5, excellent=0.2, lower_is_better=True) == 100.0
        assert _piecewise_score(0.1, target=0.5, excellent=0.2, lower_is_better=True) == 100.0

    def test_at_target_scores_70(self):
        assert _piecewise_score(0.5, target=0.5, excellent=0.2, lower_is_better=True) == 70.0

    def test_above_target_decays_toward_zero(self):
        # decay span = |target - excellent| = 0.3; value=0.8 is 1.0 decay-spans past target → 0
        score = _piecewise_score(0.8, target=0.5, excellent=0.2, lower_is_better=True)
        assert score == pytest.approx(0.0, abs=0.01)

    def test_regression_target_zero_no_division_error(self):
        """share_count_cagr_pct uses target=0, excellent=-2. Must not ZeroDivisionError."""
        # value=2 (2% dilution per year) → above target=0 → some decay
        score = _piecewise_score(2.0, target=0, excellent=-2, lower_is_better=True)
        # decay_span = |0 - -2| = 2; (2-0)/2 = 1 → 70 * (1-1) = 0
        assert score == pytest.approx(0.0)
        # And much higher dilution stays at 0, not negative
        assert _piecewise_score(100.0, target=0, excellent=-2, lower_is_better=True) == 0.0


class TestStatus:
    @pytest.mark.parametrize("score,value,expected", [
        (75, 1.0, "hit"),
        (70, 1.0, "hit"),
        (69, 1.0, "marginal"),
        (40, 1.0, "marginal"),
        (39, 1.0, "miss"),
        (0, 1.0, "miss"),
        (100, None, "na"),  # None value always na regardless of score
    ])
    def test_status_buckets(self, score, value, expected):
        assert _status(score, value) == expected
