"""Tests for pipeline orchestration helpers: load_universe, split_picks, write_csv."""
from __future__ import annotations

from pathlib import Path

import pytest

from warren_bot.pipeline import load_universe, split_picks, write_csv


class TestLoadUniverse:
    def test_basic_dedup_across_files(self, tmp_path, base_settings):
        f1 = tmp_path / "u1.txt"
        f2 = tmp_path / "u2.txt"
        f1.write_text("AAPL\nMSFT\nGOOG\n")
        f2.write_text("MSFT\nNVDA\nAAPL\n")  # MSFT and AAPL duplicate f1
        base_settings["universe"]["files"] = [str(f1), str(f2)]

        result = load_universe(base_settings)
        # Order preserved from first appearance, dupes dropped
        assert result == ["AAPL", "MSFT", "GOOG", "NVDA"]

    def test_skips_blanks_and_comments(self, tmp_path, base_settings):
        f = tmp_path / "u.txt"
        f.write_text("# header comment\n\nAAPL\n   \n# inline comment block\nMSFT\n")
        base_settings["universe"]["files"] = [str(f)]

        assert load_universe(base_settings) == ["AAPL", "MSFT"]

    def test_uppercases(self, tmp_path, base_settings):
        f = tmp_path / "u.txt"
        f.write_text("aapl\nMsft\n")
        base_settings["universe"]["files"] = [str(f)]

        assert load_universe(base_settings) == ["AAPL", "MSFT"]

    def test_missing_file_logged_not_raised(self, tmp_path, base_settings, caplog):
        f = tmp_path / "exists.txt"
        f.write_text("AAPL\n")
        base_settings["universe"]["files"] = [str(f), str(tmp_path / "nope.txt")]

        result = load_universe(base_settings)
        assert result == ["AAPL"]
        # The missing file warning should be in logs but not crash the run
        assert any("missing" in rec.message.lower() for rec in caplog.records)


def _fake_pick(ticker: str, total: float, *, error: str | None = None):
    """Build a minimal Pick stand-in for split_picks logic tests."""
    from types import SimpleNamespace

    score = SimpleNamespace(
        ticker=ticker, name=ticker, sector="Tech", total=total, dimensions=[],
        ratios=None, growth=None, valuation=SimpleNamespace(
            price=100.0, fcf_yield_pct=None, margin_of_safety_pct=None,
        ),
        error=error, data_coverage=0.85,
    )
    return SimpleNamespace(score=score, thesis=None, snap_info={})


class TestSplitPicks:
    def test_three_bucket_split(self, base_settings):
        picks = [
            _fake_pick("STRG", 80),   # strong
            _fake_pick("ANGL", 65),   # angle
            _fake_pick("PART", 50),   # partial
            _fake_pick("BELO", 30),   # below partial → dropped
        ]
        strong, angles, partial = split_picks(picks, base_settings)
        assert [p.score.ticker for p in strong] == ["STRG"]
        assert [p.score.ticker for p in angles] == ["ANGL"]
        assert [p.score.ticker for p in partial] == ["PART"]

    def test_boundary_at_strong(self, base_settings):
        # 75 is in strong; 74.9 is in angle
        picks = [_fake_pick("A", 75.0), _fake_pick("B", 74.9)]
        strong, angles, _ = split_picks(picks, base_settings)
        assert [p.score.ticker for p in strong] == ["A"]
        assert [p.score.ticker for p in angles] == ["B"]

    def test_boundary_at_angle(self, base_settings):
        picks = [_fake_pick("A", 60.0), _fake_pick("B", 59.9)]
        _, angles, partial = split_picks(picks, base_settings)
        assert [p.score.ticker for p in angles] == ["A"]
        assert [p.score.ticker for p in partial] == ["B"]

    def test_errored_picks_excluded_from_all_buckets(self, base_settings):
        picks = [
            _fake_pick("OK", 80),
            _fake_pick("BAD", 80, error="missing financials"),
        ]
        strong, angles, partial = split_picks(picks, base_settings)
        all_surfaced = strong + angles + partial
        assert all(p.score.ticker == "OK" for p in all_surfaced)

    def test_surface_limits_cap_each_bucket(self, base_settings):
        base_settings["surface_limits"] = {"strong": 2, "angles": 1, "partial": 0}
        picks = [
            _fake_pick("S1", 90),
            _fake_pick("S2", 89),
            _fake_pick("S3", 88),
            _fake_pick("A1", 70),
            _fake_pick("A2", 69),
            _fake_pick("P1", 50),
        ]

        strong, angles, partial = split_picks(picks, base_settings)

        assert [p.score.ticker for p in strong] == ["S1", "S2"]
        assert [p.score.ticker for p in angles] == ["A1"]
        assert partial == []


class TestWriteCsv:
    def test_writes_header_and_rows(self, tmp_path, base_settings):
        from types import SimpleNamespace
        # Build minimum-shape picks with dimensions for the dim score lookup
        def _pick(t, total):
            score = SimpleNamespace(
                ticker=t, name=f"{t} Co", sector="Tech", total=total,
                dimensions=[
                    SimpleNamespace(name="Moat & Profitability", score=80, cells=[]),
                    SimpleNamespace(name="Financial Strength", score=60, cells=[]),
                    SimpleNamespace(name="Consistency", score=70, cells=[]),
                    SimpleNamespace(name="Valuation / Margin of Safety", score=40, cells=[]),
                    SimpleNamespace(name="Capital Allocation", score=50, cells=[]),
                ],
                ratios=None, growth=None,
                valuation=SimpleNamespace(
                    price=150.0, fcf_yield_pct=5.5, margin_of_safety_pct=20.0,
                ),
                error=None, data_coverage=0.75,
            )
            return SimpleNamespace(score=score, thesis=None, snap_info={})

        out = tmp_path / "out.csv"
        write_csv([_pick("AAPL", 78.5), _pick("MSFT", 72.1)], out)

        lines = out.read_text().splitlines()
        assert lines[0].startswith("ticker,name,sector,total")
        assert "data_coverage" in lines[0]
        assert any("AAPL" in line and "78.5" in line for line in lines[1:])
        assert any("MSFT" in line for line in lines[1:])


class TestRunQualityWarning:
    def test_warns_when_missing_market_cap_errors_dominate(self):
        from warren_bot.cli import _data_quality_warning

        picks = [
            _fake_pick(f"BAD{i}", 0, error="below min market cap (mcap=None)")
            for i in range(30)
        ] + [_fake_pick("OK", 80)]

        warning = _data_quality_warning(picks)

        assert warning is not None
        assert "alphabet-biased" in warning

    def test_no_warning_for_small_number_of_transient_errors(self):
        from warren_bot.cli import _data_quality_warning

        picks = [_fake_pick("BAD", 0, error="below min market cap (mcap=None)")]
        picks += [_fake_pick(f"OK{i}", 80) for i in range(30)]

        assert _data_quality_warning(picks) is None

    def test_warns_when_incomplete_data_dominates(self):
        from warren_bot.cli import _data_quality_warning

        picks = [
            _fake_pick(f"INC{i}", 0, error="incomplete data: missing cashflow")
            for i in range(30)
        ] + [_fake_pick("OK", 80)]

        warning = _data_quality_warning(picks)
        assert warning is not None
        assert "incomplete statements" in warning

    def test_exclusion_summary_buckets_reasons(self):
        from warren_bot.cli import _exclusion_summary

        picks = [
            _fake_pick("A", 80),  # clean -> not counted
            _fake_pick("B", 0, error="incomplete data: missing cashflow"),
            _fake_pick("C", 0, error="incomplete data: missing balance, cashflow"),
            _fake_pick("D", 0, error="below min market cap (mcap=None)"),
            _fake_pick("E", 0, error="below min market cap (mcap=1000000)"),
        ]
        summary = _exclusion_summary(picks)
        assert summary["incomplete data"] == 2
        assert summary["missing market cap (throttled)"] == 1
        assert summary["below min market cap"] == 1
        assert "other error" not in summary
