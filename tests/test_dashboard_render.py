"""Tests for dashboard-only curation helpers."""
from __future__ import annotations

from types import SimpleNamespace

from jinja2 import Environment

from warren_bot.dashboard.render import (
    build_cockpit_data,
    _dq_badge,
    _exchange_label,
    _format_market_cap,
    _listing_badges,
    build_ai_disruption_data,
    build_kpi_rows,
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


def _cockpit_pick(ticker: str, total: float):
    score = SimpleNamespace(
        ticker=ticker,
        name=f"{ticker} Co",
        sector="Tech",
        total=total,
        effective_total=total,
        error=None,
        dimensions=[],
        valuation=SimpleNamespace(
            price=100.0,
            margin_of_safety_pct=None,
            intrinsic_value_per_share=None,
            fcf_yield_pct=None,
            shareholder_yield_pct=None,
        ),
        ratios=SimpleNamespace(
            roe_pct_avg=None,
            roic_pct_avg=None,
            gross_margin_pct_avg=None,
            net_margin_pct_avg=None,
            debt_to_equity=None,
            current_ratio=None,
            interest_coverage=None,
        ),
    )
    return SimpleNamespace(
        score=score,
        thesis=SimpleNamespace(summary=""),
        snap_info={"regularMarketPrice": 100.0},
    )


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


_DQ = {"level": "warn", "label": "Check data",
       "sources": ["edgar", "yfinance"], "flags": ["market_cap: ..."],
       "detail": ["Gap-filled: cashflow ← edgar", "market_cap: sources disagree 30.0%"]}


class TestDataQualityBadge:
    def test_badge_global_renders_in_jinja(self):
        # Exact mechanism both _TEMPLATE (AI cards) and _PICK_CARD (pick cards)
        # use: dq_badge registered as an env global.
        env = Environment(autoescape=True)
        env.globals["dq_badge"] = _dq_badge
        t = env.from_string("{{ dq_badge(dq) }}")
        out = t.render(dq=_DQ)
        assert 'class="dq-badge dq-warn"' in out
        assert "Check data" in out
        assert "Gap-filled: cashflow" in out  # detail surfaced in tooltip

    def test_badge_empty_for_unenriched_pick(self):
        assert str(_dq_badge(None)) == ""

    def test_badge_escapes_detail(self):
        bad = {"level": "info", "label": "x", "detail": ['<script>"&']}
        assert "<script>" not in str(_dq_badge(bad))

    def test_kpi_rows_carry_dq(self):
        p = SimpleNamespace(
            score=SimpleNamespace(
                ticker="AAPL", name="Apple", sector="Tech", total=80.0,
                valuation=SimpleNamespace(fcf_yield_pct=5.0, price=100.0),
                ratios=SimpleNamespace(roe_pct_avg=30.0, debt_to_equity=1.2)),
            snap_info={"regularMarketPrice": 100.0},
            dq=_DQ)
        rows = build_kpi_rows([p])
        assert rows[0]["dq"] == _DQ


class TestCockpitData:
    def test_includes_any_complete_pick_not_only_surface_scores(self):
        picks = [_cockpit_pick("HIGH", 82.0), _cockpit_pick("LOW", 22.0)]

        data = build_cockpit_data(picks)

        assert [row["t"] for row in data] == ["HIGH", "LOW"]


class TestAiDisruptionData:
    def test_requires_explicit_ai_context_not_just_tech_sector(self):
        picks = [
            _pick("GENERIC", "Enterprise software with durable margins."),
            _pick("AICLOUD", "Cloud infrastructure platform for artificial intelligence workloads."),
        ]

        data = build_ai_disruption_data(picks)

        assert [row["ticker"] for row in data["positive"]] == ["AICLOUD"]
        assert [row["ticker"] for row in data["signals"]] == ["AICLOUD"]
        assert "artificial intelligence" in data["signals"][0]["aiSignals"]
        assert "cloud" in data["signals"][0]["positiveSignals"]
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
        assert [row["ticker"] for row in data["signals"]] == ["AISTAFF"]
        assert "staffing" in data["signals"][0]["negativeSignals"]


class TestAiDisruptionNarrative:
    """Per-stock reasoning/watch text should be curated from each company's own
    signals, not the old shared boilerplate."""

    def test_reasoning_is_per_company_not_identical(self):
        picks = [
            _pick("CHIPCO", "Designs AI semiconductor and GPU accelerator chips."),
            _pick("SECCO", "AI-driven cybersecurity platform protecting cloud workloads."),
        ]
        data = build_ai_disruption_data(picks)
        rows = {r["ticker"]: r for r in data["positive"]}
        assert set(rows) == {"CHIPCO", "SECCO"}

        # Each insight names its own company and reflects its own angle.
        assert rows["CHIPCO"]["insight"].startswith("CHIPCO Co ")
        assert rows["SECCO"]["insight"].startswith("SECCO Co ")
        assert rows["CHIPCO"]["insight"] != rows["SECCO"]["insight"]
        # And the tailored "what to watch" differs too (semiconductor vs security).
        assert rows["CHIPCO"]["watch"] != rows["SECCO"]["watch"]
        # No leftover boilerplate phrasing.
        assert "Likely beneficiary because" not in rows["CHIPCO"]["insight"]

    def test_negative_watch_names_the_at_risk_activity(self):
        picks = [_pick("BPO", "AI automation across outsourcing and back office business process work.",
                       sector="Industrials")]
        data = build_ai_disruption_data(picks)
        neg = data["negative"][0]
        assert neg["insight"].startswith("BPO Co ")
        # The at-risk workflow surfaces in the watch line.
        assert any(term in neg["watch"] for term in ("outsourcing", "back office", "business process"))

    def test_company_description_is_populated(self):
        summary = "Cloud infrastructure platform for artificial intelligence workloads."
        data = build_ai_disruption_data([_pick("AICLOUD", summary)])
        assert data["positive"][0]["description"] == summary


class TestTemplateCompiles:
    def test_full_template_compiles_with_subtabs(self):
        # Guards against Jinja syntax errors in the restructured disruption card.
        from warren_bot.dashboard.render import _TEMPLATE

        env = Environment(autoescape=True)
        env.from_string(_TEMPLATE)  # raises TemplateSyntaxError on a bad edit
        assert 'data-dtab="reasoning"' in _TEMPLATE
        assert 'data-dtab="about"' in _TEMPLATE
        assert "disrupt-news" in _TEMPLATE  # news column preserved
